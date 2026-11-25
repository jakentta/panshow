"""Background worker process.

Runs separately from the drawing loop so that decoding, face detection and scaling can never
cause a dropped frame. For every 'next' request it: picks the next photo, decodes it, applies
the EXIF rotation, finds faces (or reads them from the cache), plans the camera move,
pre-scales the photo to exactly the size needed, and hands the pixels over through shared
memory.
"""
import hashlib
import json
import logging
import logging.handlers
import os
import random
import re
import threading
import time
from multiprocessing import shared_memory

import numpy as np

from . import config

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
CLOUD_ONLY = 0x1000 | 0x40000 | 0x400000   # OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS


def setup_logging(name):
    root = logging.getLogger()
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        return
    try:
        h = logging.handlers.RotatingFileHandler(
            os.path.join(config.local_dir(), name), maxBytes=1_000_000, backupCount=1, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        root.addHandler(h)
        root.setLevel(logging.INFO)
    except Exception:
        pass


def exit_with_parent():
    """Helper processes (photos, music, listening): stop at once if the main Panshow process
    goes away without telling us (closed by an installer, crashed, ended in Task Manager).
    Otherwise music could keep playing with no slideshow, and Panshow's files would stay in
    use so an update could not be installed."""
    import multiprocessing as mp
    parent = mp.parent_process()
    if parent is None:
        return

    def watch():
        try:
            parent.join()               # returns when the main process has ended
        except Exception:
            return
        logging.info("the main Panshow process has gone: this helper stops too")
        os._exit(0)
    threading.Thread(target=watch, daemon=True).start()


def _natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def scan(folder, recursive, skip_cloud, exts):
    out = []
    stack = [folder]
    cloud_count = 0
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                name = e.name
                if name.startswith(".") or name.startswith("$"):
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        if recursive:
                            stack.append(e.path)
                        continue
                    if os.path.splitext(name)[1].lower() not in exts:
                        continue
                    st = e.stat()
                    attrs = getattr(st, "st_file_attributes", 0)
                    if attrs & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM):
                        continue
                    if attrs & CLOUD_ONLY:
                        cloud_count += 1
                        if skip_cloud:
                            continue
                    if st.st_size < 4096:
                        continue
                    rel = os.path.normcase(os.path.relpath(e.path, folder))
                    out.append((e.path, rel, int(st.st_mtime), int(st.st_size), 1 if attrs & CLOUD_ONLY else 0))
                except OSError:
                    continue
    return out, cloud_count


class Order:
    """The order photos are shown in. dates: {rel: when taken} for the "date" order.
    pos_path: where the place in a date/name order is remembered; resume: start from there
    (otherwise from the beginning); once: after the last photo next() returns None instead of
    starting again."""

    def __init__(self, files, mode, rng, pos_path, dates=None, resume=False, once=False):
        self.files = files
        self.mode = mode
        self.rng = rng
        self.pos_path = pos_path
        self.once = once
        self.i = 0
        if mode == "name":
            files.sort(key=lambda f: _natural_key(f[1]))
        elif mode == "date":
            dates = dates or {}
            files.sort(key=lambda f: (dates.get(f[1], f[2]), _natural_key(f[1])))
        else:
            rng.shuffle(files)
            # Start with photos already on this PC: OneDrive "online-only" files must download first.
            local = [k for k in range(min(len(files), 2000)) if not (len(files[k]) > 4 and files[k][4])]
            for slot, k in enumerate(local[:2]):
                files[slot], files[k] = files[k], files[slot]
        self.index = {fe[1]: k for k, fe in enumerate(files)}
        if mode != "shuffle" and pos_path and resume:
            # carry on after the last photo shown last time
            try:
                with open(pos_path, "r", encoding="utf-8") as f:
                    last = f.read().strip()
                if last in self.index:
                    self.i = (self.index[last] + 1) % len(files)
            except Exception:
                pass

    def next(self):
        if self.i >= len(self.files):
            if self.once:
                return None                     # every photo has been shown: the end
            self.i = 0
            if self.mode == "shuffle" and len(self.files) > 2:
                last = self.files[-1]
                self.rng.shuffle(self.files)
                if self.files[0] is last:
                    self.files[0], self.files[-1] = self.files[-1], self.files[0]
                self.index = {fe[1]: k for k, fe in enumerate(self.files)}
        fe = self.files[self.i]
        self.i += 1
        return fe

    def shown(self, fe):
        """A new photo (or pair) is on screen: remember the place (date/name orders). Photos
        prepared in advance but never shown do not count."""
        if self.mode == "shuffle" or not self.pos_path or not fe:
            return
        rels = [fe[1][1], fe[2][1]] if fe[0] == "pair" else [fe[1]]
        known = [r for r in rels if r in self.index]
        if not known:
            return
        try:
            with open(self.pos_path, "w", encoding="utf-8") as f:
                f.write(max(known, key=lambda r: self.index[r]))      # the later one of a pair
        except Exception:
            pass


def message_image(text, sw, sh):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (sw, sh), (14, 14, 16))
    draw = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("segoeui.ttf", max(12, int(sh * 0.028)))
    except Exception:
        font = ImageFont.load_default()
    draw.multiline_text((sw / 2, sh / 2), text, anchor="mm", align="center",
                        fill=(225, 225, 225), font=font, spacing=int(sh * 0.012))
    return im


HOLD_MAX = 6        # a tall photo waits at most this many photos for a partner


def is_pair(fe):
    """File entry of two tall photos shown side by side: ("pair", entry_left, entry_right)."""
    return bool(fe) and fe[0] == "pair"


def fe_name(fe):
    if not fe:
        return "(message)"
    if is_pair(fe):
        return "%s + %s" % (fe[1][1], fe[2][1])
    return fe[1]


def _direction(plan):
    w0, w1 = plan["r0"][2], plan["r1"][2]
    return "in" if w1 < w0 * 0.995 else ("out" if w1 > w0 * 1.005 else "pan")


class Worker:
    def __init__(self, settings, screen_w, screen_h, video=False):
        from . import analysis, imaging, planner
        self.analysis, self.imaging = analysis, imaging
        self.s = settings
        self.SW, self.SH = screen_w, screen_h
        # (tests can ask for the same choices every time)
        self.rng = random.Random(int(os.environ.get("PANSHOW_TEST_SEED") or time.time_ns()))
        self.history = {}
        # "pair": two tall photos side by side; a tall photo without a partner, and panoramas,
        # use the blurred background
        self.pairs = settings.tall_photo_style == "pair"
        tall_style = "blur" if self.pairs else settings.tall_photo_style
        self.planner = planner.Planner(screen_w, screen_h, tall_style,
                                       settings.motion, settings.max_stretch,
                                       curved=settings.curved_paths, tilt=settings.tilt,
                                       flow=settings.flow_moves)
        self.half_planner = None
        if self.pairs:
            half, _ = planner.pair_layout(screen_w)
            self.half_planner = planner.Planner(half, screen_h, "pan", settings.motion, settings.max_stretch,
                                                curved=settings.curved_paths, tilt=settings.tilt, flow=False,
                                                max_tex=planner.half_texture_limit(screen_w, screen_h))
        self.half_history = {}
        self.held = None            # a tall photo waiting for a partner
        self.held_age = 0
        try:
            self.detector = analysis.FaceDetector()
        except Exception:
            logging.exception("face detector could not start; continuing without faces")
            self.detector = None
        try:
            self.objdet = analysis.ObjectDetector("object_detection_yolox_2022nov.onnx")
        except Exception:
            logging.exception("people/animal detector could not start; continuing without it")
            self.objdet = None
        folder = settings.folder
        self.cache = analysis.FaceCache(folder, config.cache_dir())
        key = hashlib.sha1(os.path.normcase(folder).encode("utf-8")).hexdigest()[:16]
        # (a video starts from the beginning and leaves the slideshow's place alone)
        self.pos_path = None if video else os.path.join(config.cache_dir(), "position_%s.txt" % key)
        lkey = hashlib.sha1(("%s|%s|%s" % (os.path.normcase(folder), settings.include_subfolders,
                                          settings.skip_cloud_only)).encode("utf-8")).hexdigest()[:16]
        self.list_path = os.path.join(config.cache_dir(), "files_%s.json" % lkey)
        self.dates_path = os.path.join(config.cache_dir(), "dates_%s.json" % lkey)
        self.fresh = None
        self.shown = set()
        files = self._load_list()
        if files:
            # Start at once with last time's file list; re-scan the folder in the background.
            threading.Thread(target=self._scan_thread, daemon=True).start()
        else:
            files = self._scan()
        self.dates = self._dates_for(files)
        self.order = Order(files, settings.order, self.rng, self.pos_path, self.dates,
                           resume=settings.resume and not video, once=settings.stop_at_end) if files else None
        self.shms = {}

    def _dates_for(self, files):
        """For the "by date" order: when each photo was taken. Remembered between runs, so only
        new or changed photos are looked at."""
        if self.s.order != "date" or not files:
            return {}
        try:
            with open(self.dates_path, "r", encoding="utf-8") as f:
                known = json.load(f)
        except Exception:
            known = {}
        t0 = time.perf_counter()
        out, todo = {}, []
        for fe in files:
            k = known.get(fe[1])
            if k and k[0] == fe[2] and k[1] == fe[3]:
                out[fe[1]] = k[2]
            else:
                todo.append(fe)
        new = len(todo)
        if todo:
            # reading the start of many files: mostly waiting for the disk, so several at once
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(8) as pool:
                found = pool.map(lambda fe: self.imaging.photo_date(fe[0], fe[1], fe[2], len(fe) > 4 and fe[4]),
                                 todo)
                for fe, when in zip(todo, found):
                    out[fe[1]] = when
        if new:
            try:
                tmp = self.dates_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({fe[1]: [fe[2], fe[3], out[fe[1]]] for fe in files}, f, separators=(",", ":"))
                os.replace(tmp, self.dates_path)
            except Exception:
                logging.exception("could not save photo dates")
        logging.info("photo dates: %d remembered, %d looked up in %.1fs", len(files) - new, new,
                     time.perf_counter() - t0)
        return out

    def _scan(self):
        s = self.s
        t0 = time.perf_counter()
        files, cloud = scan(s.folder, s.include_subfolders, s.skip_cloud_only,
                            self.imaging.EXTENSIONS) if os.path.isdir(s.folder) else ([], 0)
        logging.info("scanned %s: %d photos (%d only in the cloud) in %.2fs",
                     s.folder, len(files), cloud, time.perf_counter() - t0)
        try:
            tmp = self.list_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"folder": s.folder, "files": files}, f, separators=(",", ":"))
            os.replace(tmp, self.list_path)
        except Exception:
            logging.exception("could not save file list")
        return files

    def _scan_thread(self):
        try:
            files = self._scan()
            self.fresh = (files, self._dates_for(files))
        except Exception:
            logging.exception("background scan failed")

    def _load_list(self):
        try:
            with open(self.list_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [tuple(x) for x in data["files"]]
        except Exception:
            return []

    # ---------------------------------------------------------------- producing
    # An item is {"parts": [(plan, texture image), ...], "bg": image or None, "fe": file entry}:
    # one part (a photo filling the screen) or two (tall photos side by side).
    def produce(self):
        if self.fresh is not None:
            (files, dates), self.fresh = self.fresh, None
            if self.s.order == "shuffle":
                files = [f for f in files if f[1] not in self.shown] or files
            old = self.order
            self.dates = dates
            self.order = Order(files, self.s.order, self.rng, self.pos_path, dates,
                               once=self.s.stop_at_end) if files else None
            if self.order and old and self.s.order != "shuffle" and old.i > 0:
                # the new list carries on after the last photo taken from the old one
                last = old.files[old.i - 1][1]
                if last in self.order.index:
                    self.order.i = self.order.index[last] + 1
            elif self.order and old and self.s.stop_at_end:
                # shuffled, shown once: the photos already shown are left out of the new list,
                # so it is not started again (and may already be at its end)
                if old.i >= len(old.files):
                    self.order.i = len(self.order.files)
        if self.order is None:
            return self._message(
                "No photos found in\n%s\n\nPress S to choose your photo folder."
                % self.s.folder)
        if self.held is not None and self.held_age >= HOLD_MAX:
            fe, self.held = self.held, None             # no partner came along: show it alone
            item = self._single(fe)
            if item is not None:
                return item
        for _ in range(min(40, len(self.order.files)) + 1):
            fe = self.order.next()
            if fe is None:
                break                                         # (shown once, and that was the last)
            try:
                if self.pairs and self._is_tall(fe):
                    if self.held is None:
                        self.held, self.held_age = fe, 0      # wait for a second tall photo
                        continue
                    if self.held[1] == fe[1]:
                        continue                              # (the same photo came round again)
                    a, self.held = self.held, None
                    item = self._pair(a, fe)
                    if item is not None:
                        return item
                    continue
                item = self._single(fe, raise_errors=True)
                if self.held is not None:
                    self.held_age += 1
                return item
            except Exception as e:
                logging.warning("skipping %s: %s", fe[0], e)
        if self.held is not None:
            fe, self.held = self.held, None
            item = self._single(fe)
            if item is not None:
                return item
        if self.order.once and self.order.i >= len(self.order.files):
            logging.info("every photo has been shown: the end")
            return {"end": True}
        return self._message("Photos in\n%s\ncould not be opened." % self.s.folder)

    def _is_tall(self, fe):
        """Portrait (or square) photo? Uses the remembered size, or reads only the file header."""
        rec = self.cache.get(fe[1], fe[2], fe[3])
        if rec and rec.get("W") and rec.get("H"):
            return rec["W"] <= rec["H"]
        im, (W, H) = self.imaging.open_image(fe[0])
        im.close()
        return W <= H

    def _single(self, fe, raise_errors=False):
        try:
            t0 = time.perf_counter()
            plan, tex, bg = self._prepare(fe)
        except Exception as e:
            if raise_errors:
                raise
            logging.warning("skipping %s: %s", fe[0], e)
            return None
        self.shown.add(fe[1])
        logging.info("%s  %dx%d faces=%d kind=%s tex=%dx%d %.0fms", fe[1], plan["W"], plan["H"],
                     plan["main_faces"], plan["kind"], plan["tex"][0], plan["tex"][1],
                     (time.perf_counter() - t0) * 1000)
        return {"parts": [(plan, tex)], "bg": bg, "fe": tuple(fe)}

    def _pair(self, a, b):
        """Two tall photos side by side. If one cannot be opened, the other waits for a new partner."""
        t0 = time.perf_counter()
        try:
            pa = self._prepare(a, half=True)
        except Exception as e:
            logging.warning("skipping %s: %s", a[0], e)
            self.held, self.held_age = b, 0
            return None
        try:
            # both halves preferably move the same way (both moving in, or both out)
            pb = self._prepare(b, half=True, want_dir=_direction(pa[0]))
        except Exception as e:
            logging.warning("skipping %s: %s", b[0], e)
            self.held, self.held_age = a, 0
            return None
        self.shown.update((a[1], b[1]))
        self.history.pop("end_motion", None)   # the next photo need not continue a pair's moves
        logging.info("pair: %s (%s) + %s (%s) %.0fms", a[1], pa[0]["kind"], b[1], pb[0]["kind"],
                     (time.perf_counter() - t0) * 1000)
        return {"parts": [pa[:2], pb[:2]], "bg": None, "fe": ("pair", tuple(a), tuple(b))}

    def _message(self, text):
        im = message_image(text, self.SW, self.SH)
        W, H = self.SW, self.SH
        plan = {"W": W, "H": H, "mode": "cover", "lock": None, "w_base": W,
                "r0": (W / 2, H / 2, W), "r1": (W / 2, H / 2, W / 1.03), "kind": "message",
                "region": (0.0, 0.0, float(W), float(H)), "tex": (W, H), "bicubic": False,
                "low_res": False, "faces": [], "main_faces": 0}
        return {"parts": [(plan, im)], "bg": None, "fe": None}

    def _prepare(self, fe, half=False, want_dir=None):
        """Returns (plan, texture image, blurred background image or None)."""
        path, rel, mtime, size = fe[:4]
        imaging, analysis = self.imaging, self.analysis
        im, (W, H) = imaging.open_image(path)
        try:
            rec = self.cache.get(rel, mtime, size)
            src = icc = None
            # photos remembered by an older version lack the people/animal search (and maybe
            # the straight-line check): look at those once more
            outdated = rec is not None and (rec.get("lines") is None or
                                            (rec.get("objects") is None and self.objdet is not None))
            if rec is None or rec.get("W") != W or rec.get("H") != H or outdated:
                src, icc = imaging.decode(im)
                W, H = src.size
                rec = analysis.analyse(src, self.detector, self.objdet)
                self.cache.put(rel, mtime, size, W, H, rec)
            plan = self.plan(W, H, rec, half, want_dir)
            if src is None:
                rx0, ry0, rx1, ry1 = plan["region"]
                tw, th = plan["tex"]
                s = max(tw / (rx1 - rx0), th / (ry1 - ry0))
                src, icc = imaging.decode(im, (W * s, H * s))
            tex = imaging.make_texture(src, (W, H), plan["region"], plan["tex"], icc)
            bg = imaging.make_background(src, icc) if plan["mode"] == "contain" else None
            return plan, tex, bg
        finally:
            im.close()

    def plan(self, W, H, rec, half=False, want_dir=None):
        if not half:
            return self.planner.plan_flowing(W, H, rec.get("faces"), rec.get("sal"), self.rng, self.history,
                                             straight_lines=rec.get("lines"), objects=rec.get("objects"))
        # one half of a pair: a few tries for a move in the wanted direction (in / out)
        best = None
        for _ in range(6 if want_dir else 1):
            h = dict(self.half_history)
            h["dirs"] = list(self.half_history.get("dirs", []))
            p = self.half_planner.plan(W, H, rec.get("faces"), rec.get("sal"), self.rng, h,
                                       straight_lines=rec.get("lines"), objects=rec.get("objects"))
            ok = want_dir is None or _direction(p) == want_dir
            if best is None or ok:
                best = (p, h)
            if ok:
                break
        self.half_history.clear()
        self.half_history.update(best[1])
        return best[0]

    def produce_file(self, fe):
        """A specific photo or pair (for going back / forward through the history)."""
        try:
            t0 = time.perf_counter()
            if is_pair(fe):
                pa = self._prepare(fe[1], half=True)
                pb = self._prepare(fe[2], half=True, want_dir=_direction(pa[0]))
                item = {"parts": [pa[:2], pb[:2]], "bg": None, "fe": fe}
            else:
                plan, tex, bg = self._prepare(fe)
                item = {"parts": [(plan, tex)], "bg": bg, "fe": tuple(fe)}
            logging.info("%s  (history) %.0fms", fe_name(fe), (time.perf_counter() - t0) * 1000)
            return item
        except Exception as e:
            logging.warning("could not re-open %s: %s", fe_name(fe), e)
            return None

    # ---------------------------------------------------------------- hand-over
    def send(self, out_q, item, token=None):
        """Put the pixels in shared memory: the parts' textures one after the other, then the
        blurred background (if any)."""
        arrays = [np.asarray(img.convert("RGBA")) for _, img in item["parts"]]
        b = np.asarray(item["bg"].convert("RGBA")) if item["bg"] is not None else None
        n = sum(a.nbytes for a in arrays) + (b.nbytes if b is not None else 0)
        shm = shared_memory.SharedMemory(create=True, size=n)
        buf = np.ndarray((n,), np.uint8, buffer=shm.buf)
        parts, off = [], 0
        for (plan, _), a in zip(item["parts"], arrays):
            buf[off:off + a.nbytes] = a.reshape(-1)
            parts.append({"plan": plan, "tex": (a.shape[1], a.shape[0]), "off": off})
            off += a.nbytes
        bg = None
        if b is not None:
            buf[off:off + b.nbytes] = b.reshape(-1)
            bg = (b.shape[1], b.shape[0], off)
        del buf
        self.shms[shm.name] = shm
        out_q.put({"shm": shm.name, "parts": parts, "bg": bg, "fe": item["fe"], "token": token})

    def free(self, name):
        shm = self.shms.pop(name, None)
        if shm is not None:
            try:
                shm.close()
                shm.unlink()
            except Exception:
                pass

    def close(self):
        self.cache.save()
        for name in list(self.shms):
            self.free(name)


def worker_main(settings_dict, screen_w, screen_h, req_q, out_q, video=False):
    """video=True: preparing photos for a video file (no update checks, own log, and the
    slideshow's place in a date/name order is left alone)."""
    setup_logging("panshow-video-worker.log" if video else "panshow-worker.log")
    exit_with_parent()
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x4000)   # below normal priority
    except Exception:
        pass
    try:
        os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
        import cv2
        cv2.setNumThreads(2)
        try:
            cv2.setLogLevel(2)
        except Exception:
            pass
    except Exception:
        logging.exception("OpenCV not available")
    w = None
    try:
        settings = config.Settings(**settings_dict)
        if not video:
            # look for new versions in the background (never slows the slideshow: separate process)
            from . import update
            threading.Thread(target=update.background_loop, args=(settings.auto_update,), daemon=True).start()
        w = Worker(settings, screen_w, screen_h, video=video)
        while True:
            msg = req_q.get()
            cmd = msg[0]
            if cmd == "stop":
                break
            if cmd == "free":
                w.free(msg[1])
            elif cmd == "shown":
                if w.order is not None:
                    w.order.shown(msg[1])
            elif cmd == "next":
                item = w.produce()
                if item.get("end"):
                    out_q.put({"end": True, "token": None})     # no more photos (shown once)
                else:
                    w.send(out_q, item)
            elif cmd == "file":
                item = w.produce_file(msg[1])
                if item is None:
                    out_q.put({"failed": True, "token": msg[2]})
                else:
                    w.send(out_q, item, msg[2])
    except (KeyboardInterrupt, EOFError, BrokenPipeError):
        pass
    except Exception:
        logging.exception("worker crashed")
    finally:
        if w is not None:
            w.close()
