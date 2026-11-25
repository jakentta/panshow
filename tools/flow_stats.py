"""How much does 'flowing moves' help? Continuity between consecutive photos, on and off."""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from panshow import planner  # noqa: E402


def run(flow, tries, n=3000, seed=11):
    rng = random.Random(seed)
    p = planner.Planner(3840, 2160, "blur", "normal", 1.2, curved=True, tilt=False, flow=flow)
    hist, scores, prev = {}, [], None
    for _ in range(n):
        W, H = rng.choice([(4032, 2268), (4032, 3024), (3000, 4000), (6000, 4000)])
        faces = []
        if rng.random() < 0.5:
            fh = rng.uniform(0.08, 0.25) * H
            faces = [[rng.uniform(0.1, 0.7) * W, rng.uniform(0.1, 0.5) * H, fh * 0.8, fh, 0.9]]
        plan = p.plan_flowing(W, H, faces, [rng.random(), rng.random(), 2.0], rng, hist,
                              straight_lines=0.0, tries=tries)
        if prev is not None:
            scores.append(p.flow_score(prev, p.motion_at(plan, 0.0)))
        prev = p.motion_at(plan, 1.0)
    scores.sort()
    good = sum(1 for s in scores if s > 0.5) / len(scores)
    return scores[len(scores) // 2], good


for flow, tries in ((False, 1), (True, 6), (True, 12)):
    med, good = run(flow, tries)
    print("flowing moves %-3s (%2d tries): continuity median %+.2f, clearly continuing %3.0f%% of changes"
          % ("on" if flow else "off", tries, med, 100 * good))
