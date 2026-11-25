"""How often do the curve and the tilt actually fit, for typical phone photos on a 4K screen?"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panshow import planner  # noqa: E402

rng = random.Random(7)
for stretch in (1.0, 1.2):
    for W, H, name in ((4032, 2268, "16:9 phone photo"), (4032, 3024, "4:3 phone photo"),
                       (3000, 4000, "portrait phone photo"), (6000, 4000, "24 MP camera photo")):
        p = planner.Planner(3840, 2160, "blur", "normal", stretch, curved=True, tilt=True)
        bows = tilts = 0
        n = 400
        for _ in range(n):
            faces = []
            if rng.random() < 0.5:
                fh = rng.uniform(0.08, 0.3) * H
                faces = [[rng.uniform(0.1, 0.7) * W, rng.uniform(0.1, 0.5) * H, fh * 0.8, fh, 0.9]]
            plan = p.plan(W, H, faces, [rng.random(), rng.random(), 2.0], rng, {}, straight_lines=0.0)
            bows += bool(plan["bow"])
            tilts += bool(plan["tilt"])
        print("enlargement %.1fx  %-22s curve fits %3d%%   tilt fits %3d%%"
              % (stretch, name, 100 * bows // n, 100 * tilts // n))
