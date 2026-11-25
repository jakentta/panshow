"""Check the beat effects of Wild / Hyperdrive:
1. the zoom kick + twist never lets a photo edge into view (thousands of frames, many moments)
2. the kick and twist values per style (Wild: only a tiny bar-start kick; Hyperdrive: strong)."""
import math
import os
import random
import sys
import time
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from panshow import camera, planner  # noqa: E402
from panshow.show import Show, beat_kick  # noqa: E402
from panshow.styles import WILD_STYLES  # noqa: E402

# 1. geometry (full screens, and the tall narrow halves of side-by-side pairs, which get the
#    twist worked out for the whole screen)
rng = random.Random(9)
bad = checked = 0
for SW, SH, half in ((3840, 2160, False), (2560, 1600, False), (1920, 1080, False),
                     (3840, 2160, True), (2560, 1600, True)):
    A_screen = SW / SH
    pw = planner.pair_layout(SW)[0] if half else SW
    A = pw / SH
    p = planner.Planner(pw, SH, "pan" if half else "blur", "hyperdrive", 1.0, curved=True, tilt=True,
                        flow=not half, max_tex=planner.half_texture_limit(SW, SH) if half else None)
    st = WILD_STYLES["hyperdrive"]
    sizes = [(3024, 4032), (2268, 4032), (3000, 4000), (3600, 3600)] if half else \
        [(4032, 2268), (4032, 3024), (6000, 4000), (5400, 3600), (8000, 2000)]
    # the real pulse function, at moments around two bar starts (twist one way, then the other)
    # and an ordinary beat, with fast music (beats 0.33 s apart, so pulses overlap)
    beats = np.arange(0.0, 6.0, 1 / 3)
    down = (np.arange(len(beats)) % 4) == 0
    pulses = [beat_kick(t, beats, down, st, (SW, SH))
              for t in np.concatenate([np.arange(b - 0.08, b + 0.3, 0.01) for b in (beats[4], beats[8], beats[9])])]
    pulses = [kt for kt in pulses if kt[0] > 0]
    for _ in range(1500):
        W, H = rng.choice(sizes)
        plan = p.plan(W, H, [], [rng.random(), rng.random(), 2.0], rng, {}, straight_lines=2.0)
        if plan["mode"] != "cover":
            continue
        for u in np.linspace(0, 1, 11):
            cx, cy, w, ang = camera.at(plan, u)
            for kick, twist in pulses:
                for tw in ((twist, -twist) if half else (twist,)):     # the right half twists the other way
                    ww = w / (1 + kick)
                    corners = camera.frame_corners(cx, cy, ww, ww / A, ang + tw)
                    checked += 1
                    if min(c[0] for c in corners) < -0.5 or min(c[1] for c in corners) < -0.5 or \
                            max(c[0] for c in corners) > W + 0.5 or max(c[1] for c in corners) > H + 0.5:
                        bad += 1
print("pulse + twist frames checked: %d, photo edge in view: %d" % (checked, bad))

# 2. values per style
for name in ("wild", "hyperdrive"):
    s = types.SimpleNamespace(paused=False, style=WILD_STYLES[name], draw_size=(3840, 2160))
    k = np.arange(80)
    out = []
    for beat_index, what in ((0, "bar start"), (1, "ordinary beat")):
        s.track = {"start": time.perf_counter() - beat_index * 0.5, "beats": k * 0.5, "down": (k % 4) == 0}
        kick, twist = Show._beat_kick(s, 100.0)
        out.append("%s: kick %.1f%%, twist %.2f deg" % (what, 100 * kick, math.degrees(twist)))
    print("%-11s %s" % (name, " | ".join(out)))
sys.exit(1 if bad else 0)
