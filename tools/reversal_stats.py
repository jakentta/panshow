"""How often does the panning turn back within one photo?

The camera's sideways/up-down movement (measured in frame widths, so zooming itself does not
count) is sampled through the move. If, while it is clearly moving, its direction at one moment
points more than 90 degrees away from its direction at another moment, the pan 'turns back'."""
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panshow import camera, planner  # noqa: E402


def pan_velocities(plan, steps=40):
    out = []
    for i in range(1, steps):
        u0, u1 = (i - 0.5) / steps, (i + 0.5) / steps
        a, b = camera.at(plan, u0), camera.at(plan, u1)
        w = (a[2] + b[2]) / 2
        out.append(((b[0] - a[0]) / w * steps, (b[1] - a[1]) / w * steps))
    return out


def turns_back(plan):
    vs = [v for v in pan_velocities(plan) if math.hypot(*v) > 0.02]   # ignore near-standstill
    for i in range(len(vs)):
        for j in range(i + 1, len(vs)):
            if vs[i][0] * vs[j][0] + vs[i][1] * vs[j][1] < 0:
                return True
    return False


def run(curved, flow, n=4000, seed=21):
    rng = random.Random(seed)
    p = planner.Planner(3840, 2160, "blur", "normal", 1.2, curved=curved, tilt=False, flow=flow)
    hist, count = {}, 0
    for _ in range(n):
        W, H = rng.choice([(4032, 2268), (4032, 3024), (3000, 4000), (6000, 4000), (5400, 3600)])
        faces = []
        if rng.random() < 0.5:
            fh = rng.uniform(0.06, 0.25) * H
            faces = [[rng.uniform(0.05, 0.75) * W, rng.uniform(0.1, 0.5) * H, fh * 0.8, fh, 0.9]]
        plan = p.plan_flowing(W, H, faces, [rng.random(), rng.random(), 2.0], rng, hist, straight_lines=0.0)
        count += turns_back(plan)
    return 100.0 * count / n


if __name__ == "__main__":
    for curved, flow in ((False, False), (True, False), (False, True), (True, True)):
        print("curved path %-3s  flowing moves %-3s -> pan turns back in %5.1f%% of photos"
              % ("on" if curved else "off", "on" if flow else "off", run(curved, flow)))
