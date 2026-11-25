"""Music: plays songs from a folder in random order (own process, so it can never make the
picture stutter) and finds their beats, so photo changes can land on the beat.

Messages to the slideshow (out_q):
  ("track", title, start, beats, downbeats, duration)   a song started; `start` is when its
       first sample sounds, on the time.perf_counter() clock (shared by all processes on
       Windows); beats/downbeats are seconds into the song
  ("pos", start)                                        corrected start (after pauses, drift)
Messages from the slideshow (ctl_q): ("pause",), ("resume",), ("stop",) or ("stop", fade_seconds)
"""
import logging
import os
import queue
import random
import threading
import time

import numpy as np

EXTENSIONS = {".mp3", ".flac", ".wav", ".ogg", ".oga"}
ANALYSE_RATE = 11025
MAX_ANALYSE_SECONDS = 20 * 60
# A drum hit is noticed as soon as it enters the analysis window, about 30 ms before the
# window's centre reaches it, so the raw beat times come out early (measured: 27-34 ms on
# generated songs, tools\test_beats.py). This moves them onto the hits.
ONSET_LAG = 0.029


# ------------------------------------------------------------------ beat finding
def analyse_beats(mono, sr):
    """Find the beats of a song. mono: float32 samples at sr (about 11 kHz is plenty).
    Returns (tempo_bpm, beat_times_seconds, is_first_beat_of_bar)."""
    n_fft, hop = 1024, 128
    if len(mono) < n_fft * 8:
        return 0.0, [], []
    fps = sr / hop
    win = np.hanning(n_fft).astype(np.float32)
    # Give every part of the sound range (bass, middle, treble) equal say. Otherwise the
    # many high-frequency bins of hi-hats outweigh kick and snare, and the beat locks
    # onto the off-beats.
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    edges = np.geomspace(30.0, min(5000.0, sr * 0.45), 21)
    band = np.digitize(freqs, edges) - 1
    valid = (band >= 0) & (band < 20)
    counts = np.bincount(band[valid], minlength=20).astype(np.float32)
    weight = np.zeros(len(freqs), np.float32)
    weight[valid] = 1.0 / np.maximum(counts[band[valid]], 1.0)
    n_frames = 1 + (len(mono) - n_fft) // hop
    env = np.zeros(n_frames, np.float32)
    prev = None
    step = 2048                                   # frames per chunk (keeps memory small)
    for f0 in range(0, n_frames, step):
        f1 = min(n_frames, f0 + step)
        idx = np.arange(f0, f1)[:, None] * hop + np.arange(n_fft)[None, :]
        spec = np.log1p(100.0 * np.abs(np.fft.rfft(mono[idx] * win, axis=1)))
        full = spec if prev is None else np.vstack([prev, spec])
        flux = (np.maximum(0.0, full[1:] - full[:-1]) * weight).sum(axis=1)   # "new sound starts"
        env[f1 - len(flux):f1] = flux
        prev = spec[-1:]
    # remove the slow trend and normalise
    k = max(3, int(fps))
    env = env - np.convolve(env, np.ones(k, np.float32) / k, mode="same")
    env = np.maximum(env, 0.0)
    env /= env.std() + 1e-9

    # tempo: the repetition period of the onsets, preferring tempos near 120 BPM
    n = len(env)
    spec = np.fft.rfft(env, 2 * n)
    ac = np.fft.irfft(spec * np.conj(spec))[:n]
    bpms = np.arange(60.0, 181.0, 0.5)
    lags = fps * 60.0 / bpms
    vals = np.interp(lags, np.arange(n), ac) * np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.9) ** 2)
    bpm = float(bpms[int(np.argmax(vals))])
    period = fps * 60.0 / bpm

    # beat positions: dynamic programming (Ellis 2007): strong onsets, steady spacing
    lo, hi = int(round(period / 2)), int(round(period * 2))
    offs = np.arange(lo, hi + 1)
    pen = -100.0 * np.log(offs / period) ** 2
    score = env.astype(np.float64).copy()
    back = np.full(n, -1, np.int64)
    for t in range(lo, n):
        o = offs[offs <= t]
        cand = score[t - o] + pen[:len(o)]
        j = int(np.argmax(cand))
        if cand[j] > 0:
            score[t] = env[t] + cand[j]
            back[t] = t - o[j]
    t = int(np.argmax(score[max(0, n - hi):])) + max(0, n - hi)
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    beats = np.array(beats[::-1], np.float64)
    times = (beats * hop + n_fft / 2) / sr + ONSET_LAG
    # first beat of each bar (4/4 assumed): the phase with the strongest onsets
    strength = [env[beats[p::4].astype(int)].sum() for p in range(min(4, len(beats)))]
    phase = int(np.argmax(strength)) if strength else 0
    down = [(i - phase) % 4 == 0 for i in range(len(times))]
    return bpm, [round(float(x), 4) for x in times], down


# ------------------------------------------------------------------ live listening
class LiveBeat:
    """Finds the beat in sound that arrives a little at a time (listening to the computer's
    own output, or to a microphone) and predicts the coming beats.

    feed() takes new samples; estimate() looks at the last ~10 seconds and returns
    (sample index of a recent beat, beat period in samples, confidence, bar_offset) or None.
    bar_offset: the beats k = bar_offset, bar_offset + 4, ... counted forward from that beat
    are probably the first beat of a bar."""

    N_FFT, HOP = 1024, 128
    WINDOW_SECONDS = 10.0

    def __init__(self, sr):
        self.sr = float(sr)
        self.fps = self.sr / self.HOP
        self.win = np.hanning(self.N_FFT).astype(np.float32)
        freqs = np.fft.rfftfreq(self.N_FFT, 1.0 / self.sr)
        edges = np.geomspace(30.0, min(5000.0, self.sr * 0.45), 21)
        band = np.digitize(freqs, edges) - 1
        valid = (band >= 0) & (band < 20)
        counts = np.bincount(band[valid], minlength=20).astype(np.float32)
        self.weight = np.zeros(len(freqs), np.float32)
        self.weight[valid] = 1.0 / np.maximum(counts[band[valid]], 1.0)
        self.buf = np.zeros(0, np.float32)
        self.buf_start = 0                  # sample index of buf[0]
        self.prev = None
        self.env = np.zeros(0, np.float32)  # onset strength, one value per HOP
        self.env_end = 0                    # frame number just after the last env value
        self.level = 0.0                    # loudness of the recent sound (0 = silence)
        self.period = None                  # remembered tempo (frames), for stability
        self.bar_votes = np.zeros(4)

    def feed(self, mono):
        mono = np.asarray(mono, np.float32)
        if len(mono):
            self.level = 0.9 * self.level + 0.1 * float(np.sqrt(np.mean(mono * mono)))
        self.buf = np.concatenate([self.buf, mono])
        n = 1 + (len(self.buf) - self.N_FFT) // self.HOP if len(self.buf) >= self.N_FFT else 0
        if n <= 0:
            return
        idx = np.arange(n)[:, None] * self.HOP + np.arange(self.N_FFT)[None, :]
        spec = np.log1p(100.0 * np.abs(np.fft.rfft(self.buf[idx] * self.win, axis=1)))
        full = spec if self.prev is None else np.vstack([self.prev, spec])
        flux = (np.maximum(0.0, full[1:] - full[:-1]) * self.weight).sum(axis=1).astype(np.float32)
        if self.prev is None:
            flux = np.concatenate([[0.0], flux]).astype(np.float32)
        self.prev = spec[-1:]
        self.env = np.concatenate([self.env, flux])[-int(self.WINDOW_SECONDS * self.fps * 1.3):]
        self.env_end += n
        self.buf = self.buf[n * self.HOP:]
        self.buf_start += n * self.HOP

    def frame_to_sample(self, f):
        """Sample index at the centre of env frame f."""
        return f * self.HOP + self.N_FFT / 2

    def estimate(self):
        if self.level < 1e-4:
            return None                       # (nearly) silent
        n = min(len(self.env), int(self.WINDOW_SECONDS * self.fps))
        if n < 4 * self.fps:
            return None
        e = self.env[-n:].astype(np.float64)
        k = max(3, int(self.fps))
        e = np.maximum(e - np.convolve(e, np.ones(k) / k, mode="same"), 0.0)
        sd = e.std()
        if sd < 1e-9:
            return None
        e /= sd
        # tempo: repetition period, preferring tempos near 120 BPM and the one found before
        z = e - e.mean()                      # (without this even plain noise looks 'regular')
        spec = np.fft.rfft(z, 2 * n)
        ac = np.fft.irfft(spec * np.conj(spec))[:n]
        ac /= ac[0] + 1e-9
        bpms = np.arange(60.0, 181.0, 0.5)
        lags = self.fps * 60.0 / bpms
        raw = np.interp(lags, np.arange(n), ac)
        prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.9) ** 2)
        if self.period is not None:
            # a new song with a clearly different, stronger beat: switch at once
            free = int(np.argmax(raw * prior))
            if raw[free] > float(np.interp(self.period, np.arange(n), ac)) + 0.12:
                self.period = None
        if self.period is not None:
            prior = prior * (1.0 + 0.6 * np.exp(-0.5 * (np.log(lags / self.period) / 0.03) ** 2))
        i = int(np.argmax(raw * prior))
        P = float(lags[i])
        rhythm = float(raw[i])                # how strongly the sound repeats at that tempo
        self.period = P if self.period is None else float(np.exp(0.7 * np.log(self.period) + 0.3 * np.log(P)))
        P = self.period
        # where are the beats? try every phase, recent beats count more
        K = int((n - 1) / P)
        if K < 4:
            return None
        phis = np.arange(0.0, P, 0.5)
        pos = (n - 1) - (phis[:, None] + P * np.arange(K)[None, :])
        wts = 0.92 ** np.arange(K)
        vals = np.interp(pos, np.arange(n), e) * wts[None, :]
        score = vals.sum(axis=1)
        j = int(np.argmax(score))
        peak = (score[j] - score.mean()) / (score.std() + 1e-9)
        conf = float(min(1.0, max(0.0, rhythm * 2.5) * min(1.0, peak / 3.0)))
        last = self.env_end - 1 - phis[j]     # frame number of the most recent beat
        # first beat of the bar: the 4-beat phase with the strongest onsets (slowly averaged)
        beats = (n - 1) - phis[j] - P * np.arange(min(K, 16))
        acc = np.zeros(4)
        for b in range(len(beats)):
            acc[b % 4] += np.interp(beats[b], np.arange(n), e)
        self.bar_votes = 0.8 * self.bar_votes + 0.2 * acc / (acc.sum() + 1e-9)
        o = int(np.argmax(self.bar_votes))    # beats j = o, o+4, ... BACK from the last beat
        bar_offset = (-o) % 4                 # ... = beats k = bar_offset + 4m FORWARD
        return self.frame_to_sample(last), P * self.HOP, conf, bar_offset


def _open_source(source):
    import soundcard as sc
    if source == "output":
        return sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)
    return sc.default_microphone()


def listen(source, ctl_q=None, out_q=None, seconds=None):
    """Listen to the computer's own output (source "output") or the microphone ("mic") and
    report the beat. With out_q: sends ("grid", beat_time, period, confidence, bar_offset)
    about twice a second (beat_time on the perf_counter clock; None when no steady beat).
    With `seconds`: listens that long and returns a short summary (for the settings test)."""
    rate, dec = 48000, 4
    dev = _open_source(source)
    tracker = LiveBeat(rate / dec)
    offs = []                                  # (block arrival time - samples/rate): its minimum
    total = 0                                  # maps sample numbers onto the perf_counter clock
    carry = np.zeros(0, np.float32)
    # Windows hands over captured sound in buffers, so each piece arrives ~60-70 ms after it was
    # really played or heard (measured on this laptop: the beats came out 85-100 ms late before
    # this correction, the same through two different sound paths); a microphone also hears the
    # loudspeaker a few ms after it plays.
    delay = -0.075 if source == "output" else -0.08
    t_start = time.perf_counter()
    next_report = t_start + 4.0
    best = None
    with dev.recorder(samplerate=rate, blocksize=1024) as rec:
        while True:
            data = rec.record(numframes=1024)
            now = time.perf_counter()
            total += len(data)
            offs.append(now - total / rate)
            if len(offs) > 400:
                offs.pop(0)
            mono = np.concatenate([carry, data.mean(axis=1).astype(np.float32)])
            m = len(mono) - len(mono) % dec
            carry = mono[m:]
            tracker.feed(mono[:m].reshape(-1, dec).mean(axis=1))
            if now >= next_report:
                next_report = now + 0.5
                est = tracker.estimate()
                grid = None
                if est is not None and est[2] >= 0.25:
                    s, period, conf, bar = est
                    beat_time = min(offs) + s * dec / rate + delay
                    grid = (beat_time, period * dec / rate, conf, bar)
                    if best is None or conf > best[2]:
                        best = grid
                if out_q is not None:
                    out_q.put(("grid",) + (grid if grid else (None, 0.0, 0.0, 0)))
            if seconds is not None and now - t_start >= seconds:
                return {"level": tracker.level, "bpm": 60.0 / best[1] if best else 0.0,
                        "confidence": best[2] if best else 0.0}
            if ctl_q is not None:
                try:
                    if ctl_q.get_nowait()[0] == "stop":
                        return None
                except queue.Empty:
                    pass


def listen_main(source, ctl_q, out_q):
    from .worker import exit_with_parent, setup_logging
    setup_logging("panshow-music.log")
    exit_with_parent()
    logging.info("listening for the beat: %s", "this computer's sound" if source == "output" else "microphone")
    try:
        listen(source, ctl_q, out_q)
    except Exception:
        logging.exception("listening stopped")


def test_listen_main(source, out_q, seconds=8.0):
    """For the settings window: listen a few seconds and report what was heard."""
    from .worker import exit_with_parent
    exit_with_parent()
    try:
        out_q.put(listen(source, seconds=seconds))
    except Exception as e:
        out_q.put({"error": str(e)})


# ------------------------------------------------------------------ playing
class Track:
    def __init__(self, path):
        import soundfile as sf
        self.path = path
        self.title = os.path.splitext(os.path.basename(path))[0]
        info = sf.info(path)
        self.sr, self.channels = int(info.samplerate), min(2, int(info.channels))
        self.duration = float(info.frames) / self.sr if info.frames else 0.0
        self.bpm, self.beats, self.down = 0.0, [], []

    def analyse(self):
        """Read the song once as a small mono copy and find its beats."""
        import soundfile as sf
        step = max(1, int(round(self.sr / ANALYSE_RATE)))
        parts = []
        limit = int(MAX_ANALYSE_SECONDS * self.sr)
        read = 0
        with sf.SoundFile(self.path) as f:
            for block in f.blocks(blocksize=self.sr * 5, dtype="float32", always_2d=True):
                parts.append(block.mean(axis=1)[::step])
                read += len(block)
                if read >= limit:
                    break
        mono = np.concatenate(parts) if parts else np.zeros(0, np.float32)
        t0 = time.perf_counter()
        self.bpm, self.beats, self.down = analyse_beats(mono, self.sr / step)
        logging.info("music: %s  %.1f BPM, %d beats (%.1fs to analyse)", self.title, self.bpm,
                     len(self.beats), time.perf_counter() - t0)


class Player:
    """Plays one track through the speakers, with fades and pause."""

    FADE_IN, FADE_OUT = 1.5, 2.5

    def __init__(self, track, gain):
        import soundfile as sf
        import sounddevice as sd
        self.t = track
        self.gain = gain
        self.file = sf.SoundFile(track.path)
        self.buf = queue.Queue(maxsize=24)          # about 12 s of sound read ahead
        self.pending = np.zeros((0, track.channels), np.float32)
        self.pos = 0                                  # samples already sent to the speakers
        self.paused = False
        self.eof = False
        self.done = threading.Event()
        self.start_perf = None                        # when sample 0 sounded (perf_counter clock)
        self.stop_fade = None                         # when a stop fade-out ends...
        self.fade_len = 0.4                           # ...and how long it takes
        threading.Thread(target=self._reader, daemon=True).start()
        self.stream = sd.OutputStream(samplerate=track.sr, channels=track.channels, dtype="float32",
                                      latency="high", callback=self._callback,
                                      finished_callback=self.done.set)
        self.stream.start()

    def _reader(self):
        try:
            for block in self.file.blocks(blocksize=self.t.sr // 2, dtype="float32", always_2d=True):
                self.buf.put(block[:, :self.t.channels])
        finally:
            self.eof = True
            self.buf.put(None)

    def _callback(self, out, frames, tinfo, status):
        import sounddevice as sd
        if self.paused:
            out.fill(0)
            return
        need = frames
        chunks = []
        have = self.pending
        while len(have) < need:
            try:
                b = self.buf.get_nowait()
            except queue.Empty:
                break
            if b is None:
                self.buf.put(None)
                break
            have = np.concatenate([have, b]) if len(have) else b
        take = have[:need]
        self.pending = have[need:]
        n = len(take)
        # when does this block sound? (map the sound card's clock onto perf_counter)
        dac = tinfo.outputBufferDacTime
        now = time.perf_counter()
        sound_at = now + (dac - tinfo.currentTime if dac and tinfo.currentTime else self.stream.latency)
        start = sound_at - self.pos / self.t.sr
        if self.start_perf is None or abs(start - self.start_perf) > 0.004:
            self.start_perf = start
        # loudness: overall gain, fade in at the start, fade out at the end (or when stopping)
        idx = (self.pos + np.arange(n)) / self.t.sr
        env = np.minimum(1.0, idx / self.FADE_IN)
        if self.t.duration:
            env = np.minimum(env, np.clip((self.t.duration - idx) / self.FADE_OUT, 0.0, 1.0))
        if self.stop_fade is not None:
            left = np.clip(self.stop_fade - (now + np.arange(n) / self.t.sr), 0.0, self.fade_len) / self.fade_len
            env = np.minimum(env, left)
        out[:n] = take * (env * self.gain)[:, None]
        out[n:] = 0
        self.pos += n
        if n < frames and self.eof and self.buf.qsize() <= 1:
            raise sd.CallbackStop()

    def close(self):
        try:
            self.stream.close()
        except Exception:
            pass
        try:
            self.file.close()
        except Exception:
            pass


def scan(folder):
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if os.path.splitext(f)[1].lower() in EXTENSIONS:
                out.append(os.path.join(root, f))
    return out


def music_main(folder, volume, ctl_q, out_q):
    from .worker import exit_with_parent, setup_logging
    setup_logging("panshow-music.log")
    exit_with_parent()
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x8000)   # above normal: sound must not skip
    except Exception:
        pass
    files = scan(folder) if folder and os.path.isdir(folder) else []
    logging.info("music: %d songs in %s", len(files), folder)
    if not files:
        return
    gain = (max(0, min(100, volume)) / 100.0) ** 2
    rng = random.Random(time.time_ns())
    order = []
    nxt = {}

    def prepare_next():
        while True:
            if not order:
                order.extend(rng.sample(files, len(files)))
            path = order.pop(0)
            try:
                t = Track(path)
                t.analyse()
                nxt["t"] = t
                return
            except Exception as e:
                logging.warning("music: cannot play %s: %s", path, e)

    prepare_next()
    player = None
    stopping = False
    try:
        while not stopping:
            track = nxt.pop("t")
            loader = threading.Thread(target=prepare_next, daemon=True)
            loader.start()
            player = Player(track, gain)
            while player.start_perf is None and not player.done.is_set():
                time.sleep(0.01)
            out_q.put(("track", track.title, player.start_perf, track.beats, track.down, track.duration))
            sent = player.start_perf
            while not player.done.is_set():
                try:
                    msg = ctl_q.get(timeout=0.25)
                except queue.Empty:
                    msg = None
                if msg:
                    if msg[0] == "pause":
                        player.paused = True
                    elif msg[0] == "resume":
                        player.paused = False
                    elif msg[0] == "stop":
                        # ("stop",) quick fade; ("stop", seconds) slower, e.g. at the end of the show
                        player.fade_len = max(0.05, float(msg[1])) if len(msg) > 1 else 0.4
                        player.stop_fade = time.perf_counter() + player.fade_len
                        time.sleep(player.fade_len + 0.1)
                        stopping = True
                        break
                if player.start_perf is not None and sent is not None and abs(player.start_perf - sent) > 0.01:
                    sent = player.start_perf
                    out_q.put(("pos", sent))
            player.close()
            player = None
            loader.join()
            if "t" not in nxt:
                break
    except Exception:
        logging.exception("music stopped")
    finally:
        if player is not None:
            player.close()
