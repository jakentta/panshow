"""The live slideshow: window, frame timing, photo timeline, keyboard."""
import collections
import ctypes
import gc
import logging
import math
import multiprocessing as mp
import os
import queue
import statistics
import time
from dataclasses import asdict
from multiprocessing import shared_memory

import glfw
import moderngl
import numpy as np

from . import camera, planner, render, screens, update, version, worker

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x00000001, 0x00000002
MAX_ON_GPU = 3          # photos on the graphics card for normal play (2 in a crossfade + the next)
END_FADE = 2.0          # "stop after the last photo": the picture (and music) fade out over this


class Part:
    """One photo of a slide (a slide shows one photo, or two tall ones side by side)."""

    def __init__(self, plan, tex_size, offset):
        self.plan = plan
        self.tex_size = tuple(tex_size)
        self.offset = offset        # where its pixels start in the shared memory
        self.tex = None
        self.bg = None
        self.rows = 0


class Slide:
    """What is on screen at one time: its photo(s), with camera plans, pixels (in shared memory
    until uploaded) and textures."""

    def __init__(self, meta, req_q):
        self.meta = meta
        self.parts = [Part(p["plan"], p["tex"], p["off"]) for p in meta["parts"]]
        self.pair = len(self.parts) == 2
        self.plan = self.parts[0].plan
        self.fe = meta.get("fe")
        self.token = meta.get("token")
        self.target = None
        self.req_q = req_q
        self.shm = shared_memory.SharedMemory(name=meta["shm"])
        self.ready = False
        self.warmed = False
        self.start = None
        self.change_at = None       # show time when the next photo starts (maybe on a beat)
        self.L = None               # length of this photo's camera move

    @property
    def on_gpu(self):
        return any(p.tex is not None for p in self.parts)

    def upload(self, renderer, pool, max_bytes):
        """Upload the next piece (at most about max_bytes); returns the bytes uploaded."""
        for part in self.parts:
            tw, th = part.tex_size
            if part.rows >= th:
                continue
            if part.tex is None:
                part.tex = pool.get(tw, th)
                if part is self.parts[0] and self.meta["bg"]:
                    bw, bh, off = self.meta["bg"]
                    view = self.shm.buf[off:off + bw * bh * 4]
                    part.bg = renderer.make_texture((bw, bh), view)
                    view.release()
            row = tw * 4
            n = max(1, min(th - part.rows, max_bytes // row))
            start = part.offset + part.rows * row
            view = self.shm.buf[start:start + n * row]
            part.tex.write(view, viewport=(0, part.rows, tw, n))
            view.release()
            part.rows += n
            if all(p.rows >= p.tex_size[1] for p in self.parts):
                self._close_shm()
                self.ready = True
            return n * row
        return 0

    def _close_shm(self):
        if self.shm is not None:
            name = self.shm.name
            try:
                self.shm.close()
            except Exception:
                pass
            self.shm = None
            try:
                self.req_q.put(("free", name))
            except Exception:
                pass

    def release(self, pool):
        self._close_shm()
        for p in self.parts:
            if p.tex is not None:
                pool.put(p.tex)
                p.tex = None
            if p.bg is not None:
                p.bg.release()
                p.bg = None


def make_pool(renderer, draw_size, tall_style):
    """Textures made before the show starts (see render.TexturePool). With tall photos side by
    side, also some for the halves (tall, narrow ones)."""
    dw, dh = draw_size
    n = MAX_ON_GPU + 1          # one extra so a photo picked with the arrow keys can load during a crossfade
    if tall_style == "pair":
        return render.TexturePool(renderer, (dw * 1.5, dh * 1.5), warm_count=n, max_total=2 * n + 2,
                                  extra=(planner.half_texture_limit(dw, dh), n))
    return render.TexturePool(renderer, (dw * 1.5, dh * 1.5), warm_count=n, max_total=n)


def draw_slides(r, active, t, F, L, size, kick=0.0, twist=0.0):
    """Draw the slides on screen at show time t: the newest fades in over F seconds on top of
    the one before. A pair's two photos get their own halves; the right one twists the other
    way, so beat twists look mirrored."""
    sw, sh = size
    n = len(active)
    for i, sl in enumerate(active):
        a = 1.0 if i < n - 1 else camera.smoothstep((t - sl.start) / F)
        if a <= 0.0005:
            continue
        u = (t - sl.start) / (sl.L or L)
        if sl.pair:
            half, gap = planner.pair_layout(sw)
            r.fill((half, 0, gap, sh), a)                   # the dark line between them fades in too
            r.draw_at(sl.parts[0], u, a, kick, twist, (0, 0, half, sh))
            r.draw_at(sl.parts[1], u, a, kick, -twist, (sw - half, 0, half, sh))
        else:
            r.draw_at(sl.parts[0], u, a, kick, twist)


def change_time(t, D, F, beats=None, down=None):
    """When should the photo that starts at show time t give way to the next one?
    After D seconds, or with music (beats: beat times as show times, sorted) on a nearby beat
    (at most 15% earlier or later), preferring the first beat of a bar."""
    target = t + D
    if beats is None or not len(beats):
        return target, ""
    lo, hi = t + max(0.85 * D, F + 1.0), t + 1.15 * D
    sel = np.nonzero((beats >= lo) & (beats <= hi))[0]
    if not len(sel):
        return target, ""
    score = np.where(down[sel], 1.0, 0.55) - 0.8 * np.abs(beats[sel] - target) / (0.15 * D)
    i = sel[int(np.argmax(score))]
    return float(beats[i]), ", on a %s" % ("bar start" if down[i] else "beat")


PULSE_RISE = 0.07      # a beat pulse swells up smoothly during the last 70 ms before the beat...
PULSE_FALL = 0.18      # ...and eases away after it (about 0.2 s)


def pulse_shape(dt):
    """One beat pulse, dt seconds after the beat: 0..1, exactly 1 on the beat, smooth throughout
    (no sudden jump: a sudden zoom looks like a jolt)."""
    if dt < -PULSE_RISE or dt > 3.0 * PULSE_FALL:
        return 0.0
    if dt < 0.0:
        return 0.5 - 0.5 * math.cos(math.pi * (dt + PULSE_RISE) / PULSE_RISE)
    return math.exp(-(dt / PULSE_FALL) ** 2)


def beat_kick(t, beats, down, style, size):
    """Energetic styles with music: a gentle zoom pulse on the beat (sizes in styles.py);
    Hyperdrive also twists the picture a little on bar starts.
    beats: beat times as show times (sorted). Returns (zoom kick, twist angle in radians)."""
    if beats is None or not len(beats) or not (style["kick_bar"] or style["kick_beat"]):
        return 0.0, 0.0
    i = int(np.searchsorted(beats, t, side="right")) - 1
    sw, sh = size
    aspect = max(sw / sh, sh / sw)
    kick = twist = 0.0
    for j in (i - 1, i, i + 1):            # the pulses around now (the next one may be swelling up)
        if j < 0 or j >= len(beats):
            continue
        f = pulse_shape(t - beats[j])
        if f <= 0.0:
            continue
        bar = bool(down[j])
        k = (style["kick_bar"] if bar else style["kick_beat"]) * f
        kick += k
        if bar and style["twist"] and k > 0.0:
            # Turning the frame needs a little zoom-in to keep its corners inside the photo:
            # about aspect x angle. Using 0.4 x kick / aspect keeps the twist well inside what
            # the kick covers at every moment (both have the same shape). (The halves of a
            # pair are less wide than the screen, so this is safe for them too.)
            sign = 1.0 if int(np.count_nonzero(down[:j + 1])) % 2 else -1.0   # alternate per bar
            twist += sign * 0.4 * k / aspect
    return kick, twist


class FrameClock:
    """Animation time that advances in exact multiples of the screen's refresh period.

    Reading the real clock every frame gives tiny random errors (a few tenths of a ms) that
    show up as micro-judder in slow pans. Instead we count how many refreshes passed since the
    last frame and advance by exactly that many periods. A gentle correction keeps it aligned
    with real time.
    """

    def __init__(self, period_guess):
        self.period = period_guess
        self.t = 0.0
        self.last = None
        self.t0 = None
        self.recent = collections.deque(maxlen=120)
        self.frames = 0
        self.late = 0
        self.locked = False

    def tick(self):
        now = time.perf_counter()
        if self.last is None:
            self.last = self.t0 = now
            return self.t
        dt = now - self.last
        self.last = now
        self.frames += 1
        self.recent.append(dt)
        if not self.locked and len(self.recent) >= 90:
            self.period = statistics.median(list(self.recent)[30:])
            self.locked = True
            logging.info("refresh period measured: %.3f ms (%.1f Hz)", self.period * 1000, 1 / self.period)
        elif self.locked and self.frames % 120 == 0:
            m = statistics.median(self.recent)
            if abs(m - self.period) > 0.08 * self.period:     # the screen's refresh rate changed
                logging.info("refresh period changed: %.3f -> %.3f ms", self.period * 1000, m * 1000)
                self.period = m
        n = max(1, int(dt / self.period + 0.5))
        if self.locked:
            if n == 1 and abs(dt - self.period) < 0.2 * self.period:
                self.period += (dt - self.period) * 0.005
            elif n >= 2:
                self.late += n - 1
        self.t += min(n, 30) * self.period
        self.t += ((now - self.t0) - self.t) * 0.01
        return self.t


BLIT_FRAG = """
#version 330
uniform sampler2D u_img;
uniform vec2 u_screen;
out vec4 frag;
void main() { frag = texture(u_img, gl_FragCoord.xy / u_screen); }
"""

KEY_NAMES = {"right": glfw.KEY_RIGHT, "left": glfw.KEY_LEFT, "space": glfw.KEY_SPACE,
             "esc": glfw.KEY_ESCAPE, "s": glfw.KEY_S}


class Show:
    """mode: "fullscreen" (normal use) or "window" (testing).
    sim_size: for testing, draw at this size (e.g. 3840x2160) and shrink it into the window.
    keys: for testing, [(seconds, "right"|"left"|"space"|...)] pressed automatically."""

    def __init__(self, settings, mode="fullscreen", size=(1600, 900), seconds=None, sim_size=None,
                 swap=1, keys=None):
        self.s = settings
        self.mode = mode
        self.win_size = size
        self.seconds = seconds
        self.sim_size = sim_size
        self.swap = swap
        self.keys = sorted(keys or [])
        self.result = "quit"
        self.quit = False
        self.paused = False
        self.D = settings.seconds_per_photo
        self.F = settings.transition_seconds
        from .styles import WILD_STYLES
        self.style = WILD_STYLES.get(settings.motion)
        self.wild = self.style is not None
        if self.wild:
            self.F = min(self.F, self.style["cut"])   # quick, sharp cuts instead of slow crossfades
        self.L = self.D + self.F
        self.pending = []           # new (random) photos, prepared in advance
        self.active = []            # photos on screen (2 during a crossfade)
        self.request_times = []
        # history: every photo shown so far, so the arrow keys can go back and forward
        self.seq = []               # file entries in the order shown
        self.pos = -1               # index in seq of the photo on screen
        self.nav = None             # index the user asked to go to with the arrow keys
        self.nav_time = 0.0
        self.special = None         # a history photo being prepared / ready
        self.special_req = None     # (token, index) of the history photo asked from the worker
        self.token = 0
        self.upload_budget = 4 << 20
        self.proc = None
        self.music_proc = None
        self.music_paused = False
        self.track = None
        self.pool = None
        self.notes = []
        self.ended = False          # the photo helper has no more photos (stop after the last one)
        self.end_at = None          # show time when the final fade-out began

    # ---------------------------------------------------------------- window
    def _create_window(self):
        glfw.default_window_hints()
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, True)
        # keep showing when you click on another screen
        glfw.window_hint(glfw.AUTO_ICONIFY, False)
        if self.mode == "fullscreen":
            scr = screens.pick(self.s.display)
            if scr is None:
                raise RuntimeError("No screen found")
            logging.info("showing on %s", scr["label"])
            glfw.window_hint(glfw.REFRESH_RATE, scr["hz"])
            win = glfw.create_window(scr["w"], scr["h"], version.APP_NAME, scr["monitor"], None)
        else:
            glfw.window_hint(glfw.RESIZABLE, False)
            win = glfw.create_window(self.win_size[0], self.win_size[1],
                                     version.APP_NAME + " test window", None, None)
        if not win:
            raise RuntimeError("Could not open a window with OpenGL 3.3")
        self.win = win
        self.hwnd = glfw.get_win32_window(win)
        self.was_foreground = None
        glfw.make_context_current(win)
        glfw.swap_interval(self.swap)
        glfw.set_key_callback(win, self._on_key)
        if self.mode == "fullscreen":
            # nothing but photos: the mouse pointer is never drawn over the slideshow
            glfw.set_input_mode(win, glfw.CURSOR, glfw.CURSOR_HIDDEN)

    def _on_key(self, _w, key, _sc, action, _mods):
        if action not in (glfw.PRESS, glfw.REPEAT):
            return
        if key in (glfw.KEY_ESCAPE, glfw.KEY_Q):
            self.quit = True
        elif key == glfw.KEY_S and action == glfw.PRESS:
            self.result = "settings"
            self.quit = True
        elif key in (glfw.KEY_RIGHT, glfw.KEY_PAGE_DOWN):
            self._navigate(+1)
        elif key in (glfw.KEY_LEFT, glfw.KEY_PAGE_UP):
            self._navigate(-1)
        elif key in (glfw.KEY_SPACE, glfw.KEY_P) and action == glfw.PRESS:
            self.paused = not self.paused
            logging.info("paused" if self.paused else "resumed")
            if self.s.music_source == "output" and self.s.media_key:
                # music played by another app on this computer (Spotify, YouTube Music...):
                # press the keyboard's media play/pause key for it
                user32.keybd_event(0xB3, 0, 0, 0)          # VK_MEDIA_PLAY_PAUSE down
                user32.keybd_event(0xB3, 0, 2, 0)          # ... and up

    def _navigate(self, step):
        """Arrow keys. Pressing several times quickly jumps several photos."""
        if self.end_at is not None:
            return                                   # the show is fading out at its end
        base = self.nav if self.nav is not None else self.pos
        target = max(0, min(len(self.seq), base + step))   # len(seq) = a new random photo
        if target == self.pos:
            self.nav = None
        else:
            self.nav = target
        self.nav_time = time.perf_counter()
        logging.info("key %s -> go to photo %s (now showing %d of %d)",
                     "right" if step > 0 else "left", self.nav, self.pos, len(self.seq))

    # ---------------------------------------------------------------- worker
    def _start_worker(self, w, h):
        mpc = mp.get_context("spawn")
        self.req_q = mpc.Queue()
        self.out_q = mpc.Queue()
        self.proc = mpc.Process(target=worker.worker_main,
                                args=(asdict(self.s), w, h, self.req_q, self.out_q), daemon=True)
        self.proc.start()
        for _ in range(2):
            self.req_q.put(("next",))

    def _start_music(self):
        s = self.s
        from . import music
        if s.music_source == "folder" and s.music_folder and os.path.isdir(s.music_folder):
            target, args = music.music_main, (s.music_folder, s.music_volume)
        elif s.music_source in ("output", "mic"):
            target, args = music.listen_main, (s.music_source,)   # listen and find the beat live
        else:
            return
        mpc = mp.get_context("spawn")
        self.music_ctl = mpc.Queue()
        self.music_out = mpc.Queue()
        self.music_proc = mpc.Process(target=target, daemon=True, args=args + (self.music_ctl, self.music_out))
        self.music_proc.start()

    def _stop_music(self):
        if self.music_proc is None:
            return
        try:
            self.music_ctl.put(("stop",))          # fades out
            self.music_proc.join(0.8)
        except Exception:
            pass
        if self.music_proc.is_alive():
            self.music_proc.terminate()
        for q in (self.music_ctl, self.music_out):
            try:
                q.cancel_join_thread()
                q.close()
            except Exception:
                pass
        self.music_proc = None

    def _stop_worker(self):
        if self.proc is None:
            return
        try:
            self.req_q.put(("stop",))
            self.proc.join(0.6)
        except Exception:
            pass
        if self.proc.is_alive():
            self.proc.terminate()
        for q in (self.req_q, self.out_q):
            try:
                q.cancel_join_thread()
                q.close()
            except Exception:
                pass

    # ---------------------------------------------------------------- main
    def run(self):
        """Returns "quit" or "settings"."""
        if not glfw.init():
            raise RuntimeError("GLFW could not start")
        # keep the screen on (and Windows' own screensaver away) while photos are showing
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
        try:
            if self.mode == "fullscreen":
                scr = screens.pick(self.s.display)
                size = (scr["w"], scr["h"]) if scr else (1920, 1080)
            else:
                size = self.win_size
            draw_size = self.sim_size or size
            self._start_worker(*draw_size)          # start early: it needs a moment to load
            self._start_music()
            self._create_window()
            fbw, fbh = glfw.get_framebuffer_size(self.win)
            self.fb = (fbw, fbh)
            if not self.sim_size and (fbw, fbh) != tuple(size):
                logging.warning("framebuffer %dx%d differs from expected %dx%d", fbw, fbh, *size)
            self.draw_size = tuple(draw_size) if self.sim_size else (fbw, fbh)
            self.ctx = moderngl.create_context()
            logging.info("GL: %s | %s | window %dx%d, drawing %dx%d", self.ctx.info["GL_RENDERER"],
                         self.ctx.info["GL_VERSION"], fbw, fbh, *self.draw_size)
            self.renderer = render.Renderer(self.ctx)
            self.pool = make_pool(self.renderer, self.draw_size, self.s.tall_photo_style)
            self.warm_fbo = self.ctx.simple_framebuffer((640, 360), components=4)
            if self.sim_size:
                self.sim_tex = self.ctx.texture(self.draw_size, 4)
                self.sim_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
                self.sim_fbo = self.ctx.framebuffer(color_attachments=[self.sim_tex])
                self.blit = self.ctx.program(vertex_shader=render.VERT, fragment_shader=BLIT_FRAG)
                self.blit_vao = self.ctx.vertex_array(self.blit, [])
            self.ctx.finish()
            self.clock = FrameClock(1.0 / 60.0)
            gc.collect()
            gc.freeze()
            self._loop()
        finally:
            self._shutdown()
            kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        return self.result

    def _in_crossfade(self, t):
        return bool(self.active) and t < self.active[-1].start + self.F

    def _loop(self):
        clock = self.clock
        t = clock.tick()
        show_t = 0.0
        upload_ms = []
        gpu_q = self.ctx.query(time=True)
        gpu_ms = []
        while not self.quit:
            glfw.poll_events()
            if glfw.window_should_close(self.win):
                break
            if self.seconds and t > self.seconds:
                break
            while self.keys and t >= self.keys[0][0]:
                _, name = self.keys.pop(0)
                self._on_key(None, KEY_NAMES[name], 0, glfw.PRESS, 0)

            self.notes = []
            w0 = time.perf_counter()
            # ask the worker for the next photo, never while a crossfade runs (see _timeline)
            while self.request_times and show_t >= self.request_times[0] and not self._in_crossfade(show_t):
                self.request_times.pop(0)
                self.req_q.put(("next",))
            self._music_step(show_t)
            self._receive()
            self._upload_step(upload_ms)
            self._timeline(show_t)
            if self.end_at is not None and show_t >= self.end_at + END_FADE + 0.4:
                logging.info("the slideshow has ended (every photo shown)")
                break
            with gpu_q:
                self._draw(show_t)
            w1 = time.perf_counter()
            glfw.swap_buffers(self.win)
            self.ctx.finish()
            g = gpu_q.elapsed / 1e6
            gpu_ms.append(g)
            self.notes.append("gpu %.1fms" % g)
            late_before = clock.late
            t_prev = t
            t = clock.tick()
            # while paused, time stands still, except to finish a crossfade (e.g. after an arrow key)
            if not self.paused or self._in_crossfade(show_t):
                show_t += t - t_prev
            if clock.late > late_before:
                logging.info("late frame at t=%.2f: cpu work %.1fms, frame %.1fms, %s", t, (w1 - w0) * 1000,
                             (clock.last - w0) * 1000, "; ".join(self.notes) or "nothing special")
            if clock.frames % 3600 == 0 and clock.frames:
                logging.info("frames=%d late=%d", clock.frames, clock.late)
            if clock.frames % 30 == 0:
                fg = user32.GetForegroundWindow() == self.hwnd
                if fg != self.was_foreground:
                    # On this laptop Windows slows down a window that is not the active one
                    # (seen: 60 -> 30 frames per second), so note it in the log.
                    self.was_foreground = fg
                    logging.info("Panshow %s the active window", "is now" if fg else "is no longer")
            if clock.frames == 20:
                try:
                    update.note_ok()          # the slideshow is drawing: this version works
                except Exception:
                    logging.exception("could not note a good start")
        gpu_ms = gpu_ms[30:] or [0.0]
        gs = sorted(gpu_ms)
        self.stats = {"frames": clock.frames, "late": clock.late, "period_ms": round(clock.period * 1000, 3),
                      "max_upload_ms": round(max(upload_ms), 2) if upload_ms else 0.0,
                      "gpu_ms_avg": round(sum(gs) / len(gs), 2), "gpu_ms_p99": round(gs[int(len(gs) * 0.99)], 2),
                      "gpu_ms_max": round(gs[-1], 2), "photos_shown": len(self.seq)}
        logging.info("exit: %s", self.stats)

    def _upload_step(self, upload_ms):
        """Upload part of one photo (spread over several frames so nothing stutters)."""
        on_gpu = len(self.active) + sum(1 for p in self.pending if p.on_gpu)
        if self.special is not None and self.special.on_gpu:
            on_gpu += 1
        sl = None
        sp = self.special
        if sp is not None and not sp.ready and (sp.on_gpu or on_gpu < MAX_ON_GPU + 1):
            sl = sp                       # a photo picked with the arrow keys goes first
        else:
            for p in self.pending:
                if not p.ready and (p.on_gpu or on_gpu < MAX_ON_GPU):
                    sl = p
                    break
        if sl is None:
            return
        t0 = time.perf_counter()
        nb = sl.upload(self.renderer, self.pool, self.upload_budget)
        ms = (time.perf_counter() - t0) * 1000
        upload_ms.append(ms)
        self.notes.append("upload %dKB %.1fms" % (nb >> 10, ms))
        if ms < 1.5:
            self.upload_budget = min(24 << 20, int(self.upload_budget * 1.25))
        elif ms > 3.0:
            self.upload_budget = max(512 << 10, int(self.upload_budget * 0.6))

    def _receive(self):
        while True:
            try:
                meta = self.out_q.get_nowait()
            except queue.Empty:
                break
            except Exception:
                break
            tok = meta.get("token")
            if meta.get("end"):
                if not self.ended:
                    logging.info("no more photos: the show ends after this one")
                self.ended = True
                continue
            if meta.get("failed"):
                if self.special_req and tok == self.special_req[0]:
                    logging.info("history photo could not be opened; skipping it")
                    self.special_req = None
                    self.nav = None
                continue
            if tok is not None and not (self.special_req and tok == self.special_req[0]):
                self.req_q.put(("free", meta["shm"]))      # no longer wanted (keys pressed again)
                continue
            try:
                sl = Slide(meta, self.req_q)
            except Exception:
                logging.exception("could not take photo from worker")
                self.req_q.put(("free", meta.get("shm")))
                if tok is None:
                    self.req_q.put(("next",))
                continue
            if tok is None:
                self.pending.append(sl)
            else:
                sl.target = self.special_req[1]
                self.special = sl

    def _drop_special(self):
        if self.special is not None:
            self.special.release(self.pool)
            self.special = None
        self.special_req = None

    def _want_history(self, index, urgent, t):
        """Make sure history photo `index` is being prepared by the worker."""
        if self.special_req and self.special_req[1] == index:
            return
        if self._in_crossfade(t) or (not urgent and self.active and t < self.active[-1].start + self.F + 0.3):
            return                                   # not during a crossfade (see request_times)
        if urgent and time.perf_counter() - self.nav_time < 0.25:
            return                                   # wait: the key may be pressed again at once
        fe = self.seq[index]
        self._drop_special()
        if fe is None:                               # a message, not a photo
            self.nav = None
            return
        self.token += 1
        self.special_req = (self.token, index)
        self.req_q.put(("file", fe, self.token))

    def _timeline(self, t):
        cur = self.active[-1] if self.active else None
        target = self.nav if self.nav is not None else self.pos + 1
        if target < len(self.seq):
            # a photo seen before (after going back, the show plays forward through history)
            self._want_history(target, self.nav is not None, t)
            sp = self.special
            cand = sp if (sp is not None and sp.target == target and sp.warmed) else None
        else:
            if self.special is not None or self.special_req is not None:
                self._drop_special()
            cand = self.pending[0] if self.pending and self.pending[0].warmed else None
        if cand is not None:
            if cur is None:
                due = True
            elif self.nav is not None:
                due = t >= cur.start + self.F        # arrow key: as soon as no crossfade is running
            else:
                due = t >= cur.change_at
            if due:
                if cand is self.special:
                    self.special = None
                    self.special_req = None
                    self.pos = target
                else:
                    self.pending.pop(0)
                    self.seq.append(cand.fe)
                    self.pos = len(self.seq) - 1
                    if cand.fe:
                        self.req_q.put(("shown", cand.fe))    # (remembers the place in a date/name order)
                    # Preparing a photo keeps the processor busy, which on a laptop chip slows the
                    # graphics down. So wait until the crossfade (the most graphics work) is over.
                    self.request_times.append(t + self.F + 0.3)
                self.nav = None
                cand.start = t
                cand.change_at, on_beat = self._change_time(t)
                cand.L = cand.change_at - t + self.F
                self.active.append(cand)
                self.notes.append("photo starts")
                logging.info("showing photo %d of %d: %s  (next change in %.2fs%s)", self.pos + 1, len(self.seq),
                             worker.fe_name(cand.fe), cand.change_at - t, on_beat)
        elif self.ended and not self.pending and cur is not None and self.end_at is None and target >= len(self.seq):
            # stop after the last photo: fade out so that the picture is black just as the
            # camera move ends (or at once when Right is pressed)
            fade_start = cur.change_at + self.F - END_FADE
            if (self.nav is None and t >= fade_start) or (self.nav is not None and t >= cur.start + self.F):
                self.end_at = t
                self.nav = None
                logging.info("last photo done: fading out")
                if self.music_proc is not None and self.s.music_source == "folder":
                    try:
                        self.music_ctl.put(("stop", END_FADE))
                    except Exception:
                        pass
        while len(self.active) >= 2 and t >= self.active[-1].start + self.F:
            self.active.pop(0).release(self.pool)
            self.notes.append("old photo released")

    def _change_time(self, t):
        """When should the photo that starts now (show time t) give way to the next one?
        Normally after 'seconds per photo'. With music and beat timing: on a nearby beat
        (at most 15% earlier or later), preferring the first beat of a bar."""
        tr = self.track
        if not (self.s.beat_sync and tr and len(tr["beats"])):
            return t + self.D, ""
        # beats as show times (the music pauses whenever the show pauses, so they stay in step)
        beats = tr["start"] + tr["beats"] - time.perf_counter() + t
        return change_time(t, self.D, self.F, beats, tr["down"])

    def _beat_kick(self, t):
        """Wild / Hyperdrive with music: see beat_kick(). Returns (zoom kick, twist in radians)."""
        tr = self.track
        if self.paused or not tr or not len(tr["beats"]):
            return 0.0, 0.0
        beats = tr["start"] + tr["beats"] - time.perf_counter() + t     # as show times
        return beat_kick(t, beats, tr["down"], self.style, self.draw_size)

    def _music_step(self, show_t):
        """Take messages from the music process; pause it together with the slideshow."""
        if self.music_proc is None:
            return
        while True:
            try:
                m = self.music_out.get_nowait()
            except queue.Empty:
                break
            except Exception:
                break
            if m[0] == "track":
                self.track = {"start": m[2], "beats": np.array(m[3], np.float64),
                              "down": np.array(m[4], bool), "title": m[1]}
                logging.info("music: %s", m[1])
            elif m[0] == "pos" and self.track:
                self.track["start"] = m[1]
            elif m[0] == "grid":
                # live listening: a recent beat, the beat length and which beats start a bar
                _, beat_time, period, conf, bar = m
                if beat_time is None:
                    self.track = None              # no steady beat heard: normal timing
                else:
                    k = np.arange(int(40.0 / period) + 1)
                    self.track = {"start": beat_time, "beats": k * period, "down": (k - bar) % 4 == 0,
                                  "title": "(listening)"}
        # pause the music only once the picture really stands still (a crossfade still finishes)
        want_pause = self.paused and not self._in_crossfade(show_t)
        if want_pause != self.music_paused and self.s.music_source == "folder":
            self.music_paused = want_pause
            self.music_ctl.put(("pause",) if want_pause else ("resume",))

    def _warm(self):
        """Draw a newly loaded photo once into a tiny hidden image, long before it is shown.
        The graphics driver does some one-time work the first time a new texture is read;
        doing it now (when there is plenty of spare time) keeps the crossfade smooth."""
        for sl in ([self.special] if self.special else []) + self.pending:
            if sl.ready and not sl.warmed:
                sl.warmed = True
                self.warm_fbo.use()
                self.renderer.begin(*self.warm_fbo.size)
                for part in sl.parts:
                    x0, y0, x1, y1 = part.plan["region"]
                    self.renderer.draw(part, ((x0 + x1) / 2, (y0 + y1) / 2, x1 - x0), 1.0)
                self.notes.append("warm-up draw")
                return

    def _draw(self, t):
        r = self.renderer
        self._warm()
        if self.sim_size:
            self.sim_fbo.use()
        else:
            self.ctx.screen.use()
        r.begin(*self.draw_size)
        kick, twist = self._beat_kick(t) if self.wild else (0.0, 0.0)
        draw_slides(r, self.active, t, self.F, self.L, self.draw_size, kick, twist)
        if self.end_at is not None:
            r.fill((0, 0) + tuple(self.draw_size), min(1.0, (t - self.end_at) / END_FADE))
        if self.sim_size:
            self.ctx.screen.use()
            self.ctx.viewport = (0, 0, *self.fb)
            self.ctx.disable(moderngl.BLEND)
            self.sim_tex.use(0)
            self.blit["u_screen"].value = (float(self.fb[0]), float(self.fb[1]))
            self.blit_vao.render(moderngl.TRIANGLES, vertices=3)

    def _shutdown(self):
        try:
            for sl in self.active + self.pending + ([self.special] if self.special else []):
                try:
                    sl.release(self.pool)
                except Exception:
                    pass
            self.active, self.pending, self.special = [], [], None
            if self.pool is not None:
                self.pool.release()
        finally:
            try:
                glfw.terminate()
            except Exception:
                pass
            self._stop_music()
            self._stop_worker()
