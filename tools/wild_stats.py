"""Measure the energetic styles (Wild, Hyperdrive) on a 4K screen with typical phone photos:
how fast the camera moves, how uneven its speed is within one photo (a surge at every cut),
and how big and how sudden the beat pulses are.
Usage: wild_stats.py [seconds_per_photo] [WxH]"""
import math
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from panshow import camera, planner  # noqa: E402
from panshow.show import beat_kick  # noqa: E402
from panshow.styles import WILD_STYLES  # noqa: E402

D = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
SW, SH = (int(v) for v in (sys.argv[2] if len(sys.argv) > 2 else "3840x2160").split("x"))
PHOTOS = [(4032, 2268), (4032, 3024), (6000, 4000), (4032, 2268), (8000, 5333)]


def moves(name, n=600):
    st = WILD_STYLES[name]
    L = D + st["cut"]
    rng = random.Random(3)
    p = planner.Planner(SW, SH, "blur", name, 1.0, curved=True, tilt=True, flow=True)
    hist = {}
    pan, zoom, turn, surge = [], [], [], []
    for _ in range(n):
        W, H = rng.choice(PHOTOS)
        faces = []
        if rng.random() < 0.6:
            fh = rng.uniform(0.08, 0.3) * H
            faces = [[rng.uniform(0.1, 0.7) * W, rng.uniform(0.1, 0.5) * H, fh * 0.8, fh, 0.9]]
        plan = p.plan_flowing(W, H, faces, [rng.random(), rng.random(), 2.0], rng, hist, straight_lines=0.0)
        if plan["mode"] != "cover":
            continue
        a, b = camera.at(plan, 0.0), camera.at(plan, 1.0)
        w = (a[2] + b[2]) / 2
        # average speeds over the whole move: frame widths, zoom (log), degrees per second
        pan.append(math.hypot(b[0] - a[0], b[1] - a[1]) / w / L)
        zoom.append(abs(math.log(b[2] / a[2])) / L)
        turn.append(abs(math.degrees(b[3] - a[3])) / L)
        s0 = planner.Planner.motion_at(plan, 0.03)
        s1 = planner.Planner.motion_at(plan, 0.97)
        v0 = math.sqrt(sum(x * x for x in s0[:3]) + (s0[3] * 3) ** 2)
        v1 = math.sqrt(sum(x * x for x in s1[:3]) + (s1[3] * 3) ** 2)
        if v1 > 1e-6:
            surge.append(v0 / v1)
    return np.median(pan), np.median(zoom), np.median(turn), np.median(surge)


def pulses(name):
    st = WILD_STYLES[name]
    beats = np.arange(0.0, 8.0, 0.5)           # 120 beats per minute
    down = (np.arange(len(beats)) % 4) == 0
    t = np.arange(1.7, 2.6, 0.001)             # around a bar start at 2.0 s
    k = np.array([beat_kick(x, beats, down, st, (SW, SH))[0] for x in t])
    tw = np.array([beat_kick(x, beats, down, st, (SW, SH))[1] for x in t])
    tb = np.arange(2.2, 2.9, 0.001)            # around an ordinary beat at 2.5 s
    kb = np.array([beat_kick(x, beats, down, st, (SW, SH))[0] for x in tb])
    # suddenness: the biggest change of the pulse zoom from one frame to the next (60 frames/s)
    f = 17                                      # samples are 1 ms apart: ~one frame
    jolt = 100 * np.max(np.abs(np.log1p(k[f:]) - np.log1p(k[:-f]))) if k.max() > 0 else 0.0
    return 100 * k.max(), math.degrees(np.abs(tw).max()), 100 * kb.max(), jolt


print("seconds per photo: %.1f, screen %dx%d, typical phone and camera photos" % (D, SW, SH))
print("%-11s %8s %8s %8s %10s | %9s %9s %9s %10s" % ("style", "pan", "zoom", "tilt", "surge", "bar kick", "twist",
                                                     "beat kick", "jolt"))
print("%-11s %8s %8s %8s %10s | %9s %9s %9s %10s" % ("", "width/s", "log/s", "deg/s", "start/end", "%", "deg", "%",
                                                     "%/frame"))
for name in ("wild", "hyperdrive"):
    pan, zoom, turn, surge = moves(name)
    bar, twist, beat, jolt = pulses(name)
    print("%-11s %8.3f %8.3f %8.2f %9.1fx | %9.1f %9.2f %9.1f %10.2f" % (name, pan, zoom, turn, surge, bar, twist,
                                                                         beat, jolt))
