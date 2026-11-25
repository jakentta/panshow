"""Save the slideshow as a video file (MP4: H.264 picture, AAC sound).

The video is made frame by frame, exactly like the live show (same photo preparation, camera
moves, beat timing and drawing), but without a clock: each frame takes as long as it needs,
so nothing can stutter. Music comes from the user's own song files; photo changes (and the
Wild / Hyperdrive beat punches) land on their beats.

Runs as its own program process (Panshow.exe --video <job file>) with a small progress
window, so the settings window and the slideshow stay free.
"""
import json
import logging
import math
import os
import random
import time

import numpy as np

FPS = 60
SONG_GAP = 0.6          # seconds of silence between songs
END_FADE = 2.0          # the picture fades to black over the last seconds
MUSIC_END_FADE = 3.0    # music cut short by the chosen length fades out over this
MAX_SECONDS = 3 * 3600
SIZES = {"3840x2160": (3840, 2160), "1920x1080": (1920, 1080)}

# NV12 conversion on the graphics card (BT.709, TV levels 16-235). Rows are written bottom-up
# so that reading the result back gives the top row first, as video files want.
Y_FRAG = """
#version 330
uniform sampler2D u_src;
out vec4 frag;
void main() {
    ivec2 size = textureSize(u_src, 0);
    ivec2 p = ivec2(gl_FragCoord.xy);
    vec3 c = clamp(texelFetch(u_src, ivec2(p.x, size.y - 1 - p.y), 0).rgb, 0.0, 1.0);
    float y = dot(c, vec3(0.2126, 0.7152, 0.0722));
    frag = vec4((16.0 + 219.0 * y) / 255.0, 0.0, 0.0, 1.0);
}
"""

UV_FRAG = """
#version 330
uniform sampler2D u_src;
out vec4 frag;
vec3 px(ivec2 q, ivec2 size) {
    q = clamp(q, ivec2(0), size - 1);
    return clamp(texelFetch(u_src, ivec2(q.x, size.y - 1 - q.y), 0).rgb, 0.0, 1.0);
}
void main() {
    ivec2 size = textureSize(u_src, 0);
    ivec2 o = ivec2(gl_FragCoord.xy) * 2;      // top-left picture pixel of this colour sample
    vec3 c = vec3(0.0);
    for (int dy = 0; dy < 2; dy++)              // colour sits with the left pixel (MPEG standard)
        c += 0.25 * px(o + ivec2(-1, dy), size) + 0.5 * px(o + ivec2(0, dy), size)
             + 0.25 * px(o + ivec2(1, dy), size);
    c *= 0.5;
    float y = dot(c, vec3(0.2126, 0.7152, 0.0722));
    float cb = (c.b - y) / 1.8556;
    float cr = (c.r - y) / 1.5748;
    frag = vec4((128.0 + 224.0 * cb) / 255.0, (128.0 + 224.0 * cr) / 255.0, 0.0, 1.0);
}
"""


class VideoError(RuntimeError):
    pass


# ------------------------------------------------------------------ sound
def resample(x, sr_in, sr_out):
    """High quality sample-rate change of a whole song (n x 2 float32) with one big FFT."""
    if sr_in == sr_out or not len(x):
        return x
    n = len(x)
    m = int(round(n * sr_out / sr_in))
    N = 1 << int(math.ceil(math.log2(max(n, 2))))       # padded with silence: fast FFT, no wrap-around
    M = int(round(N * sr_out / sr_in))
    out = np.empty((m, x.shape[1]), np.float32)
    for c in range(x.shape[1]):
        X = np.fft.rfft(x[:, c], N)
        k = M // 2 + 1
        Y = X[:k] if k <= len(X) else np.concatenate([X, np.zeros(k - len(X), X.dtype)])
        out[:, c] = (np.fft.irfft(Y, M) * (M / N))[:m]
    return out


class Soundtrack:
    """The songs of a video one after another: their beats on the video's time line, and the
    sound itself (decoded one song at a time, as the video goes along)."""

    def __init__(self, paths, length=None, shuffle=False):
        self.paths = list(paths)
        if shuffle:
            random.Random(time.time_ns()).shuffle(self.paths)
        self.want_length = length
        self.songs = []
        self.beats = np.zeros(0)
        self.down = np.zeros(0, bool)
        self.length = 0.0
        self.rate = 48000
        self._cache = {}

    def analyse(self):
        """Generator: find each song's length and beats (yields (done, total) as it goes)."""
        from .music import Track
        known, bad = {}, set()
        beats, down = [], []
        t = 0.0
        i = 0
        rates = []
        total = len(self.paths)
        # all songs once (length = the music), or round and round until the chosen length
        while self.paths and i < 50 * total + 50:
            if self.want_length is None and i >= total:
                break
            if (self.want_length is not None and t >= self.want_length) or t >= MAX_SECONDS:
                break
            if len(bad) >= total:
                break
            path = self.paths[i % total]
            i += 1
            if path in bad:
                continue
            yield (min(i, total), total)
            tr = known.get(path)
            if tr is None:
                try:
                    tr = Track(path)
                    tr.analyse()
                    if tr.duration <= 1.0:
                        raise VideoError("too short")
                except Exception as e:
                    logging.warning("video: cannot use song %s: %s", path, e)
                    bad.add(path)
                    continue
                known[path] = tr
            self.songs.append({"path": path, "title": tr.title, "start": t, "dur": tr.duration, "sr": tr.sr})
            rates.append(tr.sr)
            beats.extend(t + b for b in tr.beats)
            down.extend(tr.down)
            t += tr.duration + SONG_GAP
        if not self.songs:
            raise VideoError("None of the songs could be opened.")
        self.length = t - SONG_GAP
        self.beats = np.array(beats, np.float64)
        self.down = np.array(down, bool)
        common = max(set(rates), key=rates.count)
        self.rate = common if common in (44100, 48000) else 48000
        for s in self.songs:
            s["n0"] = int(round(s["start"] * self.rate))
            s["n"] = int(round(s["dur"] * self.rate))

    def _song(self, k):
        data = self._cache.get(k)
        if data is None:
            import soundfile as sf
            s = self.songs[k]
            data, sr = sf.read(s["path"], dtype="float32", always_2d=True)
            if data.shape[1] == 1:
                data = np.repeat(data, 2, axis=1)
            data = np.ascontiguousarray(data[:, :2])
            data = resample(data, sr, self.rate)
            # tiny fades so nothing clicks where a song starts or ends
            a = min(len(data), int(0.02 * self.rate))
            b = min(len(data), int(0.4 * self.rate))
            if a:
                data[:a] *= np.linspace(0.0, 1.0, a, dtype=np.float32)[:, None]
            if b:
                data[-b:] *= np.linspace(1.0, 0.0, b, dtype=np.float32)[:, None]
            self._cache[k] = data
        return data

    def read(self, n0, count, video_length, cut_short, fade_len=MUSIC_END_FADE):
        """Sound for samples n0 .. n0+count of the video, as 16-bit stereo bytes. cut_short: the
        music is still playing when the video ends, so it fades out over its last fade_len s."""
        out = np.zeros((count, 2), np.float32)
        for k, s in enumerate(self.songs):
            a = s["n0"]
            lo, hi = max(a, n0), min(a + s["n"], n0 + count)
            if lo >= hi:
                if a + s["n"] <= n0:
                    self._cache.pop(k, None)               # finished: free its memory
                continue
            seg = self._song(k)[lo - a:hi - a]
            out[lo - n0:lo - n0 + len(seg)] += seg
        if cut_short:
            t = (n0 + np.arange(count)) / self.rate
            out *= np.clip((video_length - t) / fade_len, 0.0, 1.0).astype(np.float32)[:, None]
        return (np.clip(out, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


# ------------------------------------------------------------------ making the video
def make_video(job):
    """Generator that makes the video, yielding progress messages:
       ("status", text)                  getting ready
       ("frame", done, total)            frames written so far
       ("done", path, seconds, bytes)    finished
    Stopping the generator early (close()) cancels it and removes the unfinished file."""
    import moderngl
    import multiprocessing as mp
    import queue
    from dataclasses import asdict

    from . import config, mp4writer, render, worker
    from .show import Slide, beat_kick, change_time, draw_slides, make_pool
    from .styles import WILD_STYLES

    s = config.Settings(**job["settings"]).validated()
    W, H = SIZES.get(job.get("size"), (3840, 2160))
    out_path = job["out"]
    songs = [p for p in job.get("songs") or [] if os.path.isfile(p)]
    to_music = job.get("length") == "music"
    if to_music and not songs:
        raise VideoError("There is no music to set the length by. Choose songs, or a length in minutes.")
    if job.get("length") == "photos":
        # every photo once: the video ends after the last photo. This estimate (on the long side:
        # beat timing can make photos up to 15% longer) only plans the music.
        s.stop_at_end = True
        from .worker import scan
        from .imaging import EXTENSIONS
        n_photos = len(scan(s.folder, s.include_subfolders, s.skip_cloud_only, EXTENSIONS)[0]) \
            if os.path.isdir(s.folder) else 0
        if not n_photos:
            raise VideoError("No photos found in %s" % s.folder)
        planned = min(MAX_SECONDS, n_photos * s.seconds_per_photo * 1.2 + 10.0)
    else:
        planned = None if to_music else float(job["length"])

    sound = None
    if songs:
        sound = Soundtrack(songs, planned, shuffle=job.get("shuffle", False))
        for done, total in sound.analyse():
            yield ("status", "Listening to the music for its beat: song %d of %d…" % (done, total))
    length = sound.length + 0.5 if to_music else planned
    length = min(max(length, 3.0), MAX_SECONDS)
    cut_short = sound is not None and not to_music and sound.length > length - 0.5

    style = WILD_STYLES.get(s.motion)
    D = s.seconds_per_photo
    F = min(s.transition_seconds, style["cut"]) if style else s.transition_seconds
    L = D + F
    beats = sound.beats if (sound is not None and s.beat_sync) else None
    down = sound.down if sound is not None else None

    yield ("status", "Starting…")
    mpc = mp.get_context("spawn")
    req_q, out_q = mpc.Queue(), mpc.Queue()
    proc = mpc.Process(target=worker.worker_main, args=(asdict(s), W, H, req_q, out_q, True), daemon=True)
    proc.start()
    ctx = writer = pool = None
    active, pending = [], []
    finished = False
    try:
        ctx = moderngl.create_standalone_context(require=330)
        r = render.Renderer(ctx)
        pool = make_pool(r, (W, H), s.tall_photo_style)
        # the picture is drawn in high precision, then turned into video format (NV12)
        main_tex = ctx.texture((W, H), 4, dtype="f2")
        main_fbo = ctx.framebuffer(color_attachments=[main_tex])
        y_tex, uv_tex = ctx.texture((W, H), 1), ctx.texture((W // 2, H // 2), 2)
        y_fbo, uv_fbo = ctx.framebuffer(color_attachments=[y_tex]), ctx.framebuffer(color_attachments=[uv_tex])
        y_prog = ctx.program(vertex_shader=render.VERT, fragment_shader=Y_FRAG)
        uv_prog = ctx.program(vertex_shader=render.VERT, fragment_shader=UV_FRAG)
        y_vao, uv_vao = ctx.vertex_array(y_prog, []), ctx.vertex_array(uv_prog, [])

        for _ in range(3):
            req_q.put(("next",))

        state = {"ended": False}

        def receive(timeout):
            try:
                meta = out_q.get(timeout=timeout)
            except queue.Empty:
                if not proc.is_alive():
                    raise VideoError("The photo helper stopped unexpectedly (see panshow-video-worker.log).")
                return False
            if meta.get("end"):                 # every photo has been shown (stop after the last one)
                state["ended"] = True
                if not pending and not active:
                    raise VideoError("No photos could be found or opened in %s" % s.folder)
                return False
            sl = Slide(meta, req_q)
            if sl.plan.get("kind") == "message":
                sl.release(pool)
                raise VideoError("No photos could be found or opened in %s" % s.folder)
            while not sl.ready:
                sl.upload(r, pool, 1 << 30)
            pending.append(sl)
            return True

        while not pending:
            yield ("status", "Preparing the first photos…")
            receive(0.2)

        enc = job.get("encoder") or {}          # (only tests set this)
        rc = enc.get("rate_control", "peak")
        writer = mp4writer.Mp4Writer(out_path, W, H, FPS, audio_rate=sound.rate if sound else None,
                                     hardware=enc.get("hardware", True), bitrate=enc.get("bitrate"),
                                     rate_control=tuple(rc) if isinstance(rc, list) else rc)
        yield ("encoder", writer.used_hardware, writer.used_settings)
        total = int(round(length * FPS))
        n = writer.frames               # (frame 0, black, was written when the encoder started)
        audio_done = 0
        fade_len = MUSIC_END_FADE       # the music's fade-out at the very end
        finishing = False
        while n < total:
            t = n / FPS
            # ---- time line (as in the live show)
            cur = active[-1] if active else None
            if state["ended"] and not pending and cur is not None and not finishing \
                    and t >= cur.change_at + F - END_FADE:
                # every photo shown: fade out so the picture is black just as the last move
                # ends; the music fades out with it
                finishing = True
                length = min(length, t + END_FADE)
                total = int(round(length * FPS))
                fade_len = END_FADE
                cut_short = sound is not None
                logging.info("video: every photo shown; the video ends at %.2f s", length)
            if not finishing and (cur is None or t >= cur.change_at):
                while not pending and not state["ended"]:
                    yield ("frame", n, total)                 # waiting for the next photo
                    receive(0.1)
                if pending:     # (none after the last photo: it stays until the fade-out above)
                    cand = pending.pop(0)
                    cand.start = t
                    cand.change_at, why = change_time(t, D, F, beats, down)
                    cand.L = cand.change_at - t + F
                    active.append(cand)
                    req_q.put(("next",))
                    logging.info("video: %.3f s  %s  (next change in %.2f s%s)", t, worker.fe_name(cand.fe),
                                 cand.change_at - t, why)
                    yield ("slide", t, worker.fe_name(cand.fe))
            while len(active) >= 2 and t >= active[-1].start + F:
                active.pop(0).release(pool)
            while len(pending) < 2 and receive(0.0):
                pass
            # ---- draw
            main_fbo.use()
            r.begin(W, H)
            kick, twist = beat_kick(t, beats, down, style, (W, H)) if (style and beats is not None) else (0.0, 0.0)
            draw_slides(r, active, t, F, L, (W, H), kick, twist)
            if t > length - END_FADE:
                r.fill((0, 0, W, H), min(1.0, (t - (length - END_FADE)) / END_FADE))
            ctx.disable(moderngl.BLEND)
            main_tex.use(0)
            y_fbo.use()
            ctx.viewport = (0, 0, W, H)
            y_vao.render(moderngl.TRIANGLES, vertices=3)
            uv_fbo.use()
            ctx.viewport = (0, 0, W // 2, H // 2)
            uv_vao.render(moderngl.TRIANGLES, vertices=3)
            frame = bytearray(W * H * 3 // 2)
            y_fbo.read_into(frame, components=1, alignment=1)
            uv_fbo.read_into(frame, components=2, alignment=1, write_offset=W * H)
            writer.write_video(frame)
            n += 1
            # ---- sound, a little ahead of the picture
            if sound is not None:
                want = min(int(round(length * sound.rate)), int(n * sound.rate / FPS) + sound.rate // 10)
                if want - audio_done >= sound.rate // 10 or n >= total:
                    writer.write_audio(sound.read(audio_done, want - audio_done, length, cut_short, fade_len))
                    audio_done = want
            yield ("frame", n, total)
        if sound is not None and audio_done < int(round(length * sound.rate)):
            end = int(round(length * sound.rate))
            writer.write_audio(sound.read(audio_done, end - audio_done, length, cut_short, fade_len))
        yield ("status", "Finishing the file…")
        writer.close()
        finished = True
        size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        logging.info("video finished: %s, %.1f s, %d frames, %.1f MB", out_path, length, total, size / 1e6)
        yield ("done", out_path, length, size)
    finally:
        if writer is not None and not finished:
            writer.abort()
        for sl in active + pending:
            try:
                sl.release(pool)
            except Exception:
                pass
        try:
            req_q.put(("stop",))
            proc.join(2.0)
        except Exception:
            pass
        if proc.is_alive():
            proc.terminate()
        for q in (req_q, out_q):
            try:
                q.cancel_join_thread()
                q.close()
            except Exception:
                pass
        if pool is not None:
            pool.release()
        if ctx is not None:
            ctx.release()
        if not finished and os.path.exists(out_path):
            try:
                os.remove(out_path)                   # an unfinished file would not play anyway
                logging.info("unfinished video removed: %s", out_path)
            except Exception:
                logging.exception("could not remove the unfinished video")


# ------------------------------------------------------------------ the job and its window
def write_job(settings, out, size, length, songs, shuffle):
    """Called by the settings window: save the job for the video process; returns its path."""
    from dataclasses import asdict

    from . import config
    path = os.path.join(config.local_dir(), "video-job-%d.json" % time.time_ns())
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"settings": asdict(settings), "out": out, "size": size, "length": length,
                   "songs": list(songs), "shuffle": bool(shuffle)}, f)
    return path


def start_job(job_path):
    """Start the video process (this same program with --video)."""
    import subprocess
    import sys
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--video", job_path]
    else:
        main = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Panshow.py")
        cmd = [sys.executable, main, "--video", job_path]
    subprocess.Popen(cmd, close_fds=True)


def _fmt_time(sec):
    sec = int(round(sec))
    if sec >= 3600:
        return "%d h %02d min" % (sec // 3600, sec % 3600 // 60)
    if sec >= 60:
        return "%d min %02d s" % (sec // 60, sec % 60)
    return "%d s" % sec


def run_job(job_path):
    """The video process: a small progress window while the video is made."""
    import ctypes
    import tkinter as tk
    from tkinter import messagebox, ttk

    from . import config
    k32 = ctypes.windll.kernel32
    # tells a quitting slideshow not to install an update now (that would stop this process)
    k32.CreateMutexW.restype = ctypes.c_void_p
    mutex = k32.CreateMutexW(None, False, config.VIDEO_MUTEX)
    # keep the computer awake (the screen may switch off) and leave it responsive
    k32.SetThreadExecutionState(0x80000000 | 0x00000001)
    try:
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x4000)       # below normal
    except Exception:
        pass
    try:
        with open(job_path, "r", encoding="utf-8") as f:
            job = json.load(f)
    finally:
        try:
            os.remove(job_path)
        except OSError:
            pass
    logging.info("video job: %s, size %s, length %s, %d songs", job.get("out"), job.get("size"),
                 job.get("length"), len(job.get("songs") or []))

    root = tk.Tk()
    root.title("Panshow: making a video")
    root.resizable(False, False)
    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")
    try:
        from .analysis import resource_path
        root.iconbitmap(resource_path("panshow.ico"))
    except Exception:
        pass
    px = root.winfo_fpixels("1i") / 96.0
    frm = ttk.Frame(root, padding=16)
    frm.grid(sticky="nsew")
    ttk.Label(frm, text="Making the video:  %s" % os.path.basename(job["out"])).grid(row=0, column=0, sticky="w")
    status = tk.StringVar(value="Starting…")
    ttk.Label(frm, textvariable=status, width=70, wraplength=int(460 * px)).grid(row=1, column=0, sticky="w",
                                                                               pady=(8, 4))
    bar = ttk.Progressbar(frm, length=int(460 * px), mode="indeterminate")
    bar.grid(row=2, column=0, sticky="ew")
    bar.start(15)
    note = tk.StringVar(value="You can keep using your computer. The slideshow may not run smoothly "
                              "while a video is being made.")
    ttk.Label(frm, textvariable=note, foreground="#666", wraplength=int(460 * px)).grid(
        row=3, column=0, sticky="w", pady=(8, 0))
    btns = ttk.Frame(frm)
    btns.grid(row=4, column=0, sticky="e", pady=(14, 0))
    cancel_btn = ttk.Button(btns, text="Cancel")
    cancel_btn.pack(side="left")

    gen = make_video(job)
    state = {"running": True, "t0": None, "n0": 0, "result": None}

    def finish_ui(text, ok_path=None):
        state["running"] = False
        bar.stop()
        bar.config(mode="determinate")
        bar.config(value=float(bar.cget("maximum")) if ok_path else 0)
        status.set(text)
        note.set("")
        cancel_btn.pack_forget()
        if ok_path:
            def show():
                explorer = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "explorer.exe")
                import subprocess
                subprocess.Popen([explorer, "/select,", os.path.normpath(ok_path)])
            ttk.Button(btns, text="Show in folder", command=show).pack(side="left", padx=4)
        ttk.Button(btns, text="Close", command=root.destroy).pack(side="left")

    def tick():
        if not state["running"]:
            return
        end = time.perf_counter() + 0.12
        try:
            while time.perf_counter() < end:
                msg = next(gen)
                if msg[0] == "status":
                    status.set(msg[1])
                    break
                if msg[0] == "frame":
                    _, n, total = msg
                    if state["t0"] is None:
                        state["t0"], state["n0"] = time.perf_counter(), n
                        bar.stop()
                        bar.config(mode="determinate")
                    bar.config(maximum=total, value=n)       # (the total shrinks if the photos run out)
                    if n % 15 == 0 or n == total:
                        el = time.perf_counter() - state["t0"]
                        speed = (n - state["n0"]) / el if el > 0.5 else 0.0
                        left = (total - n) / speed if speed > 0 else 0.0
                        status.set("Frame %s of %s (%d%%)%s" % (
                            format(n, ","), format(total, ","), 100 * n // max(1, total),
                            ("  ·  about %s left" % _fmt_time(left)) if speed > 0 and el > 3 else ""))
                elif msg[0] == "done":
                    _, path, secs, size = msg
                    amount = ("%.1f GB" % (size / 1e9)) if size >= 1e9 else ("%d MB" % (size // 1_000_000))
                    finish_ui("Finished: a %s video (%s), saved as\n%s\nin the folder %s" % (
                        _fmt_time(secs), amount, os.path.basename(path), os.path.dirname(path)), path)
                    return
        except StopIteration:
            finish_ui("Finished.")
            return
        except VideoError as e:
            finish_ui("Could not make the video:\n%s" % e)
            return
        except Exception as e:
            logging.exception("video failed")
            finish_ui("Could not make the video: %s\n\nDetails are in %s" % (e, os.path.join(
                config.local_dir(), "panshow-video.log")))
            return
        root.after(1, tick)

    def cancel():
        if not state["running"]:
            root.destroy()
            return
        if messagebox.askyesno("Panshow", "Stop making the video? The unfinished file is removed.", parent=root):
            state["running"] = False
            try:
                gen.close()
            except Exception:
                logging.exception("problem while cancelling")
            root.destroy()

    cancel_btn.config(command=cancel)
    root.protocol("WM_DELETE_WINDOW", cancel)
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
    root.geometry("+%d+%d" % (x, y))
    root.after(50, tick)
    root.mainloop()
    try:
        gen.close()
    except Exception:
        pass
    k32.SetThreadExecutionState(0x80000000)
    if mutex:
        k32.CloseHandle(ctypes.c_void_p(mutex))
    return 0
