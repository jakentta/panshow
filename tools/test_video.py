"""End-to-end test of "Save as video".

Makes a short video from a photo folder with a generated drum song, then reads it back with
PyAV (a TEST-ONLY tool, never shipped: its ready-made build contains GPL encoders) and checks:
length, frame rate, sound, that photo changes land on the song's beats, and that the sound
lines up with the song.

Usage: test_video.py <photo_folder> <out_dir> <folder_containing_pyav> [WxH] [motion] [tall_style] [seconds]
Run it with throw-away APPDATA / LOCALAPPDATA (it writes a face cache there).
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from panshow import config, video  # noqa: E402

SR = 44100


def drum_song(path, bpm=120.0, seconds=24.0, offset=0.25):
    rng = np.random.default_rng(5)
    out = np.zeros(int((seconds + 2) * SR))
    beat = 60.0 / bpm
    beats = np.arange(offset, seconds, beat)
    t = np.arange(int(0.3 * SR)) / SR
    kick = np.sin(2 * np.pi * 55 * t) * np.exp(-t * 18) * 0.9
    tn = np.arange(int(0.2 * SR)) / SR
    snare = rng.standard_normal(len(tn)) * np.exp(-tn * 25) * 0.4
    for i, b in enumerate(beats):
        p = int(b * SR)
        out[p:p + len(kick)] += kick * (1.0 if i % 4 == 0 else 0.6)
        if i % 2 == 1:
            out[p:p + len(snare)] += snare
    out = out[:int(seconds * SR)]
    import soundfile as sf
    sf.write(path, np.stack([out, out], axis=1).astype(np.float32), SR)
    return beats


def main():
    folder, out_dir, av_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    size = sys.argv[4] if len(sys.argv) > 4 else "1920x1080"
    motion = sys.argv[5] if len(sys.argv) > 5 else "strong"
    tall = sys.argv[6] if len(sys.argv) > 6 else "pair"
    seconds = float(sys.argv[7]) if len(sys.argv) > 7 else 24.0
    os.makedirs(out_dir, exist_ok=True)
    song = os.path.join(out_dir, "drums.wav")
    truth = drum_song(song, seconds=seconds)
    s = config.Settings(folder=folder, seconds_per_photo=3.0, transition_seconds=0.4, motion=motion,
                        tall_photo_style=tall, order="shuffle", beat_sync=True).validated()
    out = os.path.join(out_dir, "test_%s_%s_%s.mp4" % (size, motion, tall))
    job = {"settings": s.__dict__, "out": out, "size": size, "length": "music", "songs": [song], "shuffle": False}
    t0 = time.perf_counter()
    slides, frames_t0, n_frames = [], None, 0
    for msg in video.make_video(job):
        if msg[0] == "slide":
            slides.append(msg[1])
        elif msg[0] == "frame":
            if frames_t0 is None:
                frames_t0 = time.perf_counter()
            n_frames = msg[1]
        elif msg[0] == "done":
            print("made %s: %.1f s, %.1f MB, %.1f s to make (%.1f frames/s while drawing)" % (
                os.path.basename(out), msg[2], msg[3] / 1e6, time.perf_counter() - t0,
                n_frames / (time.perf_counter() - frames_t0)))
    # 1. photo changes on the beat (from the video maker's own time line)
    # (a change can only happen on a frame: up to 1/60 s after the beat; plus the beat finder's
    # own small error)
    changes = np.array(slides[1:])
    on_beat = 0
    offs = []
    for ct in changes:
        k = int(np.argmin(np.abs(truth - ct)))
        off = ct - truth[k]
        offs.append(1000 * off)
        on_beat += -0.012 <= off <= 1 / 60 + 0.012
    print("photo changes: %d, on a beat: %d   (ms after the drum hit: %s)" % (
        len(changes), on_beat, " ".join("%+.0f" % o for o in offs)))

    # 2. read the file back
    sys.path.append(av_dir)
    import av
    with av.open(out) as c:
        vs = c.streams.video[0]
        print("video: %s %dx%d, %s fps, %d frames, %.2f s" % (vs.codec_context.name, vs.codec_context.width,
              vs.codec_context.height, vs.average_rate, vs.frames, float(vs.duration * vs.time_base)))
        diffs, prev, times = [], None, []
        for f in c.decode(vs):
            g = f.to_ndarray(width=240, height=136, format="gray").astype(np.float32)
            if prev is not None:
                diffs.append(np.abs(g - prev).mean())
                times.append(float(f.pts * vs.time_base))
            prev = g
    with av.open(out) as c:
        a = c.streams.audio[0]
        pcm = []
        for f in c.decode(a):
            x = f.to_ndarray()
            pcm.append(x[0] if x.ndim == 2 else x)
        pcm = np.concatenate(pcm).astype(np.float32)
        rate = a.codec_context.sample_rate
    print("sound: %s %d Hz, %.2f s" % (a.codec_context.name, rate, len(pcm) / rate))
    # 3. picture: the crossfades should start at the logged change times
    d = np.array(diffs)
    times = np.array(times)
    base = np.median(d)
    starts = []
    for ct in changes:
        k = int(np.searchsorted(times, ct))
        win = d[max(0, k - 3):k + 30]
        starts.append(win.max() / max(base, 1e-3))
    print("picture changes seen at the logged times (crossfade stronger than 2x normal motion): %d of %d"
          % (sum(1 for v in starts if v > 2.0), len(starts)))
    # 4. sound lines up with the song (lag between the file's sound and the original)
    import soundfile as sf
    orig, _ = sf.read(song, dtype="float32", always_2d=True)
    orig = orig[:, 0]
    if rate != SR:
        orig = video.resample(orig[:, None], SR, rate)[:, 0]
    n = min(len(orig), len(pcm), rate * 10)
    A = np.fft.rfft(pcm[:n], 2 * n)
    B = np.fft.rfft(orig[:n], 2 * n)
    cc = np.fft.irfft(A * np.conj(B))
    lag = int(np.argmax(np.concatenate([cc[-rate // 5:], cc[:rate // 5]]))) - rate // 5
    print("sound lag against the song: %+.1f ms" % (1000 * lag / rate))


if __name__ == "__main__":
    main()
