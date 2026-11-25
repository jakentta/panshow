"""Property test for the shot planner: random photos + random faces, check the rules hold,
with and without the curved camera path and the tilt."""
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from panshow import camera, planner  # noqa: E402
import reversal_stats  # noqa: E402

STEPS = 20


def check(n=20000, seed=3):
    rng = random.Random(seed)
    problems = {}
    kinds = {}
    face_ok = face_total = 0
    with_bow = with_tilt = tilt_wanted = 0
    tilt_on_straight_photo = 0
    max_bow = max_tilt = 0.0
    subject_kinds = {}
    flow_scores = []
    history = {}
    halves = 0
    for i in range(n):
        SW, SH = rng.choice([(2560, 1600), (3840, 2160), (1920, 1080), (152, 112)])
        W = rng.choice([640, 1200, 2000, 3000, 4032, 6000, 8000, 12000])
        H = int(W / rng.choice([1.0, 4 / 3, 3 / 2, 16 / 9, 3 / 4, 2 / 3, 9 / 16, 3.0, 0.4]))
        style = rng.choice(["blur", "pan"])
        motion = rng.choice(["gentle", "normal", "strong", "wild", "hyperdrive"])
        stretch = rng.choice([1.0, 1.0, 1.2, 1.4])
        curved, tilt, flow = rng.random() < 0.7, rng.random() < 0.6, rng.random() < 0.7
        max_tex = None
        if W <= H and rng.random() < 0.4:
            # one half of two tall photos side by side (as the worker plans them)
            max_tex = planner.half_texture_limit(SW, SH)
            SW = planner.pair_layout(SW)[0]
            style, flow = "pan", False
            halves += 1
        p = planner.Planner(SW, SH, style, motion, stretch, curved=curved, tilt=tilt, flow=flow, max_tex=max_tex)
        faces = []
        for _ in range(rng.choice([0, 0, 1, 1, 1, 2, 3, 6])):
            fh = rng.uniform(0.02, 0.35) * min(W, H)
            fw = fh * rng.uniform(0.7, 1.0)
            faces.append([rng.uniform(0, W - fw), rng.uniform(0, H - fh), fw, fh, 0.9])
        sal = [rng.random(), rng.random(), rng.uniform(1, 4)]
        lines = rng.choice([None, 0.0, 0.2, 0.5, 1.5, 3.0])
        objects = []
        for _ in range(rng.choice([0, 0, 1, 1, 2, 3])):
            ow = rng.uniform(0.08, 0.6) * W
            oh = min(H * 0.98, ow * rng.uniform(0.6, 2.8))
            objects.append([rng.uniform(0, W - ow), rng.uniform(0, H - oh), ow, oh,
                            rng.uniform(0.3, 0.95), rng.choice([0, 0, 15, 16, 17])])
        plan = p.plan_flowing(W, H, faces, sal, rng, history, straight_lines=lines, objects=objects)
        for k in plan["main_subjects"]:
            subject_kinds[k] = subject_kinds.get(k, 0) + 1
        if "flow_score" in plan:
            flow_scores.append(plan["flow_score"])
        kinds[plan["kind"]] = kinds.get(plan["kind"], 0) + 1
        A = SW / SH
        g = p._geo(W, H)
        if tilt:
            tilt_wanted += 1
        if plan.get("bow"):
            with_bow += 1
            max_bow = max(max_bow, math.hypot(*plan["bow"]))
        if plan.get("tilt"):
            with_tilt += 1
            max_tilt = max(max_tilt, abs(plan["tilt"][0]), abs(plan["tilt"][1]))
            if (lines or 0) >= planner.STRAIGHT_LINES_LIMIT and motion not in ("wild", "hyperdrive"):
                tilt_on_straight_photo += 1

        def bad(msg):
            problems[msg] = problems.get(msg, 0) + 1

        rx0, ry0, rx1, ry1 = plan["region"]
        tol = 1e-6 * max(W, H) + 0.6
        main_face = max(faces, key=lambda f: f[3]) if plan["main_faces"] == 1 else None
        face_box = None
        if main_face and plan["kind"] in ("push_face", "pull_face"):
            sb = planner._face_boxes(main_face, W, H)[0]
            if all(camera.box_in_frame(sb, *camera.at(plan, u)[:3], camera.at(plan, u)[2] / A,
                                       camera.at(plan, u)[3]) for u in (0.0, 1.0)):
                face_box = sb
        for k in range(STEPS + 1):
            u = k / STEPS
            cx, cy, w, ang = camera.at(plan, u)
            h = w / A
            corners = camera.frame_corners(cx, cy, w, h, ang)
            xs, ys = [c[0] for c in corners], [c[1] for c in corners]
            if plan["mode"] == "cover":
                if min(xs) < -tol or min(ys) < -tol or max(xs) > W + tol or max(ys) > H + tol:
                    bad("edge of the photo comes into view (%s)" % plan["mode"])
                if min(xs) < rx0 - tol or max(xs) > rx1 + tol or min(ys) < ry0 - tol or max(ys) > ry1 + tol:
                    bad("view outside texture region")
            else:
                if ang:
                    bad("tilt used with blurred background")
                for lo, ext, S, axis in ((cx - w / 2, w, W, "x"), (cy - h / 2, h, H, "y")):
                    if ext <= S + tol:
                        if lo < -tol or lo + ext > S + tol:
                            bad("view leaves photo on %s axis (contain)" % axis)
                    elif lo > tol or lo + ext < S - tol:
                        bad("photo not centred/contained on %s axis (contain)" % axis)
            if w < g.w_min * 0.999 or w > g.w_base * 1.001:
                bad("zoom outside allowed range (sharpness limit)")
            if face_box is not None and not camera.box_in_frame(face_box, cx, cy, w, h, ang, tol=1.0):
                bad("face cut by the frame edge during the move")
            for kb in plan["keep"]:
                if not camera.box_in_frame(kb, cx, cy, w, h, ang, tol=1.0):
                    bad("a subject that should stay in view was cut during the move")
                    break
        tw, th = plan["tex"]
        if max(tw, th) > planner.MAX_TEX:
            bad("texture too large")
        if max_tex and (tw > max_tex[0] or th > max_tex[1]):
            bad("half-screen texture bigger than the ones made at start-up")
        if max_tex and plan["mode"] != "cover":
            bad("half of a pair not filling its half")
        rw = rx1 - rx0
        f_max = max(plan["r0"][2], plan["r1"][2]) * (tw / rw) / SW
        if f_max > 2.05:
            bad("texture footprint > 2 (%.2f)" % f_max)
        if main_face and plan["kind"] in ("push_face", "pull_face"):
            for u in (0.0, 1.0):
                cx, cy, w, ang = camera.at(plan, u)
                face_total += 1
                if camera.box_in_frame(main_face[:2] + [main_face[0] + main_face[2], main_face[1] + main_face[3]],
                                       cx, cy, w, w / A, ang, tol=1.0):
                    face_ok += 1
        if camera.log_zoom_ratio(plan["r0"], plan["r1"]) > 0.75:
            bad("zoom change too big (> 2.1x)")
        if reversal_stats.turns_back(plan):
            bad("pan direction turns back within one photo")
    print("kinds:", dict(sorted(kinds.items())))
    print("halves of side-by-side pairs among them: %d" % halves)
    print("single-face shots with face fully visible at both ends: %d / %d" % (face_ok, face_total))
    print("curved path used: %d of %d plans (largest bend %.1f%% of frame width)" % (with_bow, n, 100 * max_bow))
    print("tilt used: %d of %d plans where it was switched on (largest %.2f degrees); on photos with "
          "strong straight lines: %d" % (with_tilt, tilt_wanted, max_tilt, tilt_on_straight_photo))
    print("subjects aimed at:", dict(sorted(subject_kinds.items())))
    if flow_scores:
        fs = sorted(flow_scores)
        print("flowing moves: %d plans, continuity score median %.2f (1 = same direction and speed)"
              % (len(fs), fs[len(fs) // 2]))
    print("problems:", problems or "none")
    return not problems


if __name__ == "__main__":
    sys.exit(0 if check() else 1)
