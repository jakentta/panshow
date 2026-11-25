"""Test the live beat finder: feed generated songs piece by piece (as if playing live) and check
how fast it locks on and how well it PREDICTS the coming beats."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from panshow import music  # noqa: E402
import test_beats  # noqa: E402  (generated songs)

SR = 12000
test_beats.SR = SR


def run(segments, label):
    """segments: list of (audio, true_beat_times_in_that_audio)."""
    lb = music.LiveBeat(SR)
    audio = np.concatenate([a for a, _ in segments])
    truth, offset = [], 0.0
    for a, beats in segments:
        truth.extend(beats + offset)
        offset += len(a) / SR
    truth = np.array(truth)
    block, pos, next_check = 256, 0, 4.0
    errors, first_lock, conf_seen = [], None, []
    while pos + block <= len(audio):
        lb.feed(audio[pos:pos + block])
        pos += block
        t = pos / SR
        if t >= next_check:
            next_check += 0.5
            est = lb.estimate()
            if est is None or est[2] < 0.25:
                conf_seen.append(0.0)
                continue
            s, period, conf, bar = est
            conf_seen.append(conf)
            # predict the beats of the next 6 seconds and compare with the real ones
            pred = (s + period * np.arange(1, 40)) / SR
            pred = pred[(pred > t) & (pred < t + 6.0)]
            if len(truth) and len(pred):
                err = np.abs(pred[:, None] - truth[None, :]).min(axis=1)
                if first_lock is None and np.median(err) < 0.07:
                    first_lock = t
                errors.append(np.median(err))
    errors = np.array(errors)
    good = np.mean(errors < 0.07) if len(errors) else 0.0
    print("%-34s locked after %5s | predictions within 70 ms: %3.0f%% | confidence median %.2f"
          % (label, "%.1fs" % first_lock if first_lock else "never", 100 * good,
             np.median(conf_seen) if conf_seen else 0))
    return errors


for bpm, style in ((123, "dance"), (96, "rock"), (140, "dance"), (75, "soft"), (110, "soft")):
    a, b = test_beats.song(bpm, 60, style)
    run([(a, b)], "%s %d BPM" % (style, bpm))
a1, b1 = test_beats.song(120, 40, "dance")
a2, b2 = test_beats.song(96, 40, "rock")
errs = run([(a1, b1), (a2, b2)], "120 BPM then a 96 BPM song")
rng = np.random.default_rng(1)
lb = music.LiveBeat(SR)
lb.feed(np.zeros(SR * 12, np.float32))
print("%-34s %s" % ("silence", "no beat reported (correct)" if lb.estimate() is None else "WRONG: beat reported"))
lb = music.LiveBeat(SR)
lb.feed((rng.standard_normal(SR * 12) * 0.1).astype(np.float32))
est = lb.estimate()
print("%-34s %s" % ("plain noise (no rhythm)", "no beat reported (correct)" if est is None or est[2] < 0.25
                    else "WRONG: beat with confidence %.2f" % est[2]))
