"""Test the beat finder on generated songs with known tempo and beat times."""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from panshow import music  # noqa: E402

SR = 11025
rng = np.random.default_rng(3)


def tone(freq, dur, decay):
    t = np.arange(int(dur * SR)) / SR
    return np.sin(2 * np.pi * freq * t) * np.exp(-t * decay)


def noise(dur, decay):
    t = np.arange(int(dur * SR)) / SR
    return rng.standard_normal(len(t)) * np.exp(-t * decay)


def song(bpm, seconds, style, start_offset=0.37):
    out = np.zeros(int(seconds * SR) + 2 * SR)
    beat = 60.0 / bpm
    beats = np.arange(start_offset, seconds, beat)
    kick, snare, hat = tone(55, 0.3, 18) * 0.9, noise(0.2, 25) * 0.4, noise(0.05, 80) * 0.15
    for i, b in enumerate(beats):
        p = int(b * SR)
        if style in ("dance", "rock"):
            if style == "dance" or i % 2 == 0:
                out[p:p + len(kick)] += kick
            if i % 2 == 1:
                out[p:p + len(snare)] += snare
            h = int((b + beat / 2) * SR)
            out[h:h + len(hat)] += hat
        else:  # "soft": quiet piano-like chords, accent on each bar's first beat
            chord = sum(tone(f, 1.2, 2.5) for f in (220, 277, 330)) * (0.5 if i % 4 == 0 else 0.25)
            out[p:p + len(chord)] += chord
    bass = np.sin(2 * np.pi * 82 * np.arange(len(out)) / SR) * 0.05     # a constant hum
    out += bass + rng.standard_normal(len(out)) * 0.01
    return out[:int(seconds * SR)].astype(np.float32), beats


if __name__ == "__main__":
    for bpm, style in ((123, "dance"), (96, "rock"), (140, "dance"), (75, "soft"), (110, "soft")):
        audio, truth = song(bpm, 180, style)
        t0 = time.perf_counter()
        found_bpm, beats, down = music.analyse_beats(audio, SR)
        dt = time.perf_counter() - t0
        beats = np.array(beats)
        # for each true beat, distance to the nearest found beat
        near = np.abs(truth[:, None] - beats[None, :]).min(axis=1) if len(beats) else np.full(len(truth), 9.9)
        hit = np.mean(near < 0.07)
        # signed timing error of the found beats (should be close to 0: not early, not late)
        d = beats[:, None] - truth[None, :]
        err = d[np.arange(len(beats)), np.argmin(np.abs(d), axis=1)] if len(beats) else np.zeros(1)
        bias = 1000 * np.median(err[np.abs(err) < 0.1]) if np.any(np.abs(err) < 0.1) else 999.0
        tempo_ok = min(abs(found_bpm - bpm), abs(found_bpm - 2 * bpm), abs(found_bpm - bpm / 2)) < 2
        print("%-5s %3d BPM -> found %5.1f BPM %s | %3.0f%% of beats within 70 ms | timing %+.0f ms %s | "
              "%.2fs for 3 minutes" % (style, bpm, found_bpm, "(ok)" if tempo_ok else "(WRONG)", 100 * hit, bias,
                                       "(ok)" if abs(bias) <= 12 else "(OFF)", dt))
