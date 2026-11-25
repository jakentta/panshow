"""Shot planning: decide how the camera moves over one photo.

All coordinates are in pixels of the upright (EXIF-rotated) original photo.
A rectangle is (cx, cy, w); its height is w / A where A is the screen aspect ratio.
"""
import math

from . import camera
from .styles import WILD_STYLES

MAX_TOTAL_ZOOM = 2.6     # never zoom closer than this relative to the "whole photo" framing
MAX_TEX = 8192           # largest texture side we upload
MAX_MOVE_ZOOM = 1.92     # largest zoom change within one move
# Photos with long horizontal/vertical lines (horizons, buildings, door frames) are never
# tilted: even a fraction of a degree makes them look crooked. See analysis.straight_lines().
STRAIGHT_LINES_LIMIT = 0.6
# A curved path bends sideways by at most this part of the distance the camera travels, so the
# direction of the pan changes gently (about 30 degrees each way) and never turns back.
BOW_PER_TRAVEL = 0.2
# Two tall photos side by side: the dark line between them, as a part of the screen width
PAIR_GAP = 0.006


def pair_layout(sw):
    """Width of each half and of the gap between them, in pixels (left half starts at 0,
    right half at sw - half)."""
    gap = max(2, int(round(sw * PAIR_GAP)))
    half = (sw - gap) // 2
    return half, sw - 2 * half


def half_texture_limit(sw, sh):
    """Largest texture used for one half of a pair. Bigger ones are scaled down to this, so
    the slideshow can make all its textures before the show starts (see TexturePool)."""
    half, _ = pair_layout(sw)
    return int(half * 1.6), int(sh * 2.0)


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


class Geo:
    """Geometry helper for one photo on one screen."""

    def __init__(self, W, H, A, mode, lock, w_base, w_min):
        self.W, self.H, self.A = W, H, A
        self.mode, self.lock = mode, lock
        self.w_base, self.w_min = w_base, w_min

    def clamp_w(self, w):
        return min(max(w, self.w_min), self.w_base)

    def clamp_c(self, cx, cy, w):
        h = w / self.A
        if self.lock == "x":
            cx = self.W / 2
        else:
            lo, hi = sorted((w / 2, self.W - w / 2))
            cx = min(max(cx, lo), hi)
        if self.lock == "y":
            cy = self.H / 2
        else:
            lo, hi = sorted((h / 2, self.H - h / 2))
            cy = min(max(cy, lo), hi)
        return (cx, cy, w)

    def place(self, px, py, w, fx, fy):
        """Frame so that point (px, py) appears at fraction (fx, fy) of the screen."""
        w = self.clamp_w(w)
        return self.clamp_c(px - (fx - 0.5) * w, py - (fy - 0.5) * w / self.A, w)

    def fit_width(self, box, fill_x, fill_y):
        return max((box[2] - box[0]) / fill_x, (box[3] - box[1]) * self.A / fill_y)

    def rect_box(self, r):
        cx, cy, w = r
        h = w / self.A
        return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

    def contains(self, r, box, tol=0.5):
        x0, y0, x1, y1 = self.rect_box(r)
        return box[0] >= x0 - tol and box[1] >= y0 - tol and box[2] <= x1 + tol and box[3] <= y1 + tol

    def frame_box(self, box, w, fx, fy, anchor):
        """Frame `anchor` at (fx, fy), but make sure the whole `box` stays in view. If the box
        is too big for the frame in one direction, the anchor (a head) decides instead."""
        w = self.clamp_w(max(w, self.fit_width(box, 0.94, 0.94)))
        cx, cy, w = self.place(anchor[0], anchor[1], w, fx, fy)
        h = w / self.A
        if box[2] - box[0] <= w:
            if box[0] < cx - w / 2:
                cx = box[0] + w / 2
            if box[2] > cx + w / 2:
                cx = box[2] - w / 2
        if box[3] - box[1] <= h:
            if box[1] < cy - h / 2:
                cy = box[1] + h / 2
            if box[3] > cy + h / 2:
                cy = box[3] - h / 2
        return self.clamp_c(cx, cy, w)

    def overlap_frac(self, r, box):
        x0, y0, x1, y1 = self.rect_box(r)
        ix = max(0.0, min(x1, box[2]) - max(x0, box[0]))
        iy = max(0.0, min(y1, box[3]) - max(y0, box[1]))
        area = max(1e-6, (box[2] - box[0]) * (box[3] - box[1]))
        return ix * iy / area

    def resolve_cuts(self, r, must, others, fx, fy, anchor):
        """Avoid faces sliced by the frame edge: include them, or nudge them out of view."""
        for _ in range(4):
            cut = [b for b in others if 0.04 < self.overlap_frac(r, b) < 0.97]
            if not cut:
                return r
            b = cut[0]
            # Option 1: nudge the frame so the cut face leaves the view.
            cx, cy, w = r
            x0, y0, x1, y1 = self.rect_box(r)
            options = []
            if self.lock != "x":
                options += [(cx + (b[2] - x0) + 1, cy), (cx - (x1 - b[0]) - 1, cy)]
            if self.lock != "y":
                options += [(cx, cy + (b[3] - y0) + 1), (cx, cy - (y1 - b[1]) - 1)]
            best = None
            for ox, oy in options:
                rr = self.clamp_c(ox, oy, w)
                if all(self.contains(rr, m) for m in must) and self.overlap_frac(rr, b) <= 0.04:
                    d = abs(rr[0] - cx) + abs(rr[1] - cy)
                    if best is None or d < best[0]:
                        best = (d, rr)
            if best is not None and best[0] < 0.25 * w:
                r = best[1]
                continue
            # Option 2: widen the frame so the face is fully included.
            must = must + [b]
            ub = _union(must)
            r = self.frame_box(ub, w, fx, fy, anchor)
            if not self.contains(r, b):
                return r  # cannot fix (face at the photo edge); accept
        return r


def _face_boxes(f, W, H):
    x, y, w, h = f[:4]
    sb = (max(0.0, x - 0.3 * w), max(0.0, y - 0.45 * h),
          min(W, x + 1.3 * w), min(H, y + 1.35 * h))
    anchor = (x + w / 2, y + 0.45 * h)
    return sb, anchor


class Subj:
    """Something the camera can aim at: a face, a person's body (face not visible) or an animal.
    sb: box that should be in view when possible; tight: box to frame when moving in close;
    anchor: the point placed on the rule-of-thirds lines; size: face height (faces only)."""
    __slots__ = ("sb", "tight", "anchor", "size", "is_face", "kind")

    def __init__(self, sb, tight, anchor, size, is_face, kind):
        self.sb, self.tight, self.anchor, self.size, self.is_face, self.kind = sb, tight, anchor, size, is_face, kind

    def area(self):
        return (self.sb[2] - self.sb[0]) * (self.sb[3] - self.sb[1])


def _face_subj(f, W, H):
    sb, anchor = _face_boxes(f, W, H)
    return Subj(sb, sb, anchor, f[3], True, "face")


def _object_subj(o, W, H):
    x, y, w, h = o[:4]
    box = (max(0.0, x), max(0.0, y), min(float(W), x + w), min(float(H), y + h))
    if int(o[5]) == 0:
        # a person whose face is not visible (from behind, far away, side-on): aim at the head,
        # and when moving in close keep head and upper body in view
        tight = (box[0], max(0.0, y - 0.03 * h), box[2], min(float(H), y + 0.55 * h))
        return Subj(box, tight, (x + w / 2, y + 0.13 * h), 0.0, False, "person")
    return Subj(box, box, (x + w / 2, y + 0.42 * h), 0.0, False, "animal")


class Planner:
    def __init__(self, screen_w, screen_h, tall_style="blur", motion="normal", max_stretch=1.0,
                 curved=True, tilt=False, flow=False, max_tex=None):
        self.SW, self.SH = screen_w, screen_h
        self.max_tex = max_tex              # (w, h): never make a bigger texture than this
        self.A = screen_w / screen_h
        self.tall_style = tall_style
        self.style = WILD_STYLES.get(motion)
        self.wild = self.style is not None
        if self.wild:
            self.motion = self.style["motion"]
        else:
            self.motion = {"gentle": 0.6, "normal": 1.0, "strong": 1.45}.get(motion, 1.0)
        # Wild / Hyperdrive: big tilts need room, and fast motion hides a little softness, so
        # allow some enlargement; tilting is always on (also on horizons: a deliberate look).
        self.max_stretch = max(max_stretch, self.style["stretch"]) if self.wild else max_stretch
        self.curved = curved
        self.tilt = tilt or self.wild
        self.flow = flow

    # ------------------------------------------------------------------ helpers
    def _geo(self, W, H):
        A = self.A
        ratio = (W / H) / A
        tall, wide = ratio < 0.8, ratio > 1.9
        mode, lock = "cover", None
        if (tall or wide) and self.tall_style == "blur":
            mode = "contain"
            lock = "x" if tall else "y"
        if mode == "cover":
            w_base = min(W, H * A)
        else:
            w_base = max(W, H * A)
        w_sharp = self.SW / self.max_stretch
        w_min = max(w_sharp, w_base / MAX_TOTAL_ZOOM)
        low_res = False
        if w_min >= w_base * 0.97:
            # Low resolution photo (e.g. an old scan): allow only a very small, gentle zoom.
            low_res = True
            w_min = w_base / (1.0 + 0.06 * self.motion)
        if mode == "contain":
            # keep the whole photo width (tall) / height (wide) visible so it stays centred
            w_min = max(w_min, W if lock == "x" else H * A)
        w_min = min(w_min, w_base)
        g = Geo(W, H, A, mode, lock, w_base, w_min)
        g.tall, g.wide, g.low_res = tall, wide, low_res
        return g

    def _fx(self, g, px, strength=1.0):
        n = px / g.W - 0.5
        return 0.5 + max(-1 / 6, min(1 / 6, n * strength))

    def _fy(self, g, py, strength=1.0):
        n = py / g.H - 0.5
        return 0.5 + max(-1 / 6, min(1 / 6, n * strength))

    def _zr(self, rng):
        # (at most MAX_MOVE_ZOOM: the photo is pre-scaled for the closest view, and much more
        # than 2x further out its fine detail would start to shimmer)
        return min(MAX_MOVE_ZOOM, rng.uniform(1.0 + 0.12 * self.motion, 1.0 + 0.32 * self.motion))

    @staticmethod
    def _pick(rng, weights):
        items = [(k, w) for k, w in weights.items() if w > 0]
        total = sum(w for _, w in items)
        x = rng.random() * total
        for k, w in items:
            x -= w
            if x <= 0:
                return k
        return items[-1][0]

    # ------------------------------------------------------------------ main
    def _subjects(self, W, H, faces, objects):
        """Who to aim at. Faces first; pets that are big next to the faces join them. Without
        faces: the most prominent people (seen without a face) and animals. Everything else
        is only kept from being cut in half by the frame edge. Returns (subjects, others)."""
        main, minor = [], []
        if faces:
            big = max(f[3] for f in faces)
            # far-away "crowd" faces only: treat them as an interesting area, not as subjects
            if big >= 0.022 * min(W, H):
                for f in faces:
                    (main if f[3] >= 0.45 * big else minor).append(f)
        subjects = [_face_subj(f, W, H) for f in main]
        others = [_face_subj(f, W, H).sb for f in minor]
        objs = []
        for o in objects or []:
            if len(o) < 6 or o[4] < 0.5:
                continue
            x, y, w, h = o[:4]
            if int(o[5]) == 0 and any(x <= f[0] + f[2] / 2 <= x + w and y <= f[1] + f[3] / 2 <= y + h
                                      for f in faces):
                continue                                   # this person's face was already found
            s = _object_subj(o, W, H)
            if s.area() >= 0.012 * W * H:                  # only reasonably big ones
                objs.append(s)
        if subjects:
            face_h = max(s.sb[3] - s.sb[1] for s in subjects)
            for s in objs:
                if s.kind == "animal" and s.sb[3] - s.sb[1] >= 0.8 * face_h:
                    subjects.append(s)                     # e.g. a person with their dog
                else:
                    others.append(s.sb)
        elif objs:
            biggest = max(s.area() for s in objs)
            for s in objs:
                if s.area() >= 0.3 * biggest:
                    subjects.append(s)
                else:
                    others.append(s.sb)
        subjects.sort(key=lambda s: s.anchor[0])
        return subjects, others

    # ------------------------------------------------------------------ flowing moves
    @staticmethod
    def motion_at(plan, u, du=0.01):
        """How the camera moves at time u: (sideways, up/down, zoom, turn) per unit of time,
        the first two in frame widths, zoom on the log scale, turn in radians."""
        a = camera.at(plan, max(0.0, u - du))
        b = camera.at(plan, min(1.0, u + du))
        dt = min(1.0, u + du) - max(0.0, u - du)
        w = (a[2] + b[2]) / 2
        return ((b[0] - a[0]) / w / dt, (b[1] - a[1]) / w / dt,
                (math.log(b[2]) - math.log(a[2])) / dt, (b[3] - a[3]) / dt)

    @staticmethod
    def flow_score(prev_end, start):
        """How well a move that starts with `start` continues one that ended with `prev_end`."""
        wts = (1.0, 1.0, 1.0, 3.0)
        a = [p * k for p, k in zip(prev_end, wts)]
        b = [s * k for s, k in zip(start, wts)]
        na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
        if na < 1e-4 or nb < 1e-4:
            return 0.0
        cos = sum(x * y for x, y in zip(a, b)) / (na * nb)
        return cos - 0.25 * abs(math.log(nb / na))          # same direction, similar speed

    def plan_flowing(self, W, H, faces, saliency, rng, history, straight_lines=None, objects=None,
                     tries=10):
        """Like plan(), but when 'flowing moves' is on, try a few different moves and pick the
        one that best continues the previous photo's camera movement (as in film editing)."""
        prev = history.get("end_motion")
        if not self.flow or prev is None:
            plan = self.plan(W, H, faces, saliency, rng, history, straight_lines, objects)
        else:
            cands = []
            for _ in range(tries):
                h = dict(history)
                h["dirs"] = list(history.get("dirs", []))
                p = self.plan(W, H, faces, saliency, rng, h, straight_lines, objects)
                cands.append((self.flow_score(prev, self.motion_at(p, 0.0)), p, h))
            cands.sort(key=lambda c: -c[0])
            pick = cands[1] if len(cands) > 1 and rng.random() < 0.2 else cands[0]  # a little variety
            history.clear()
            history.update(pick[2])
            plan = pick[1]
            plan["flow_score"] = round(pick[0], 3)
        history["end_motion"] = self.motion_at(plan, 1.0)
        return plan

    def plan(self, W, H, faces, saliency, rng, history=None, straight_lines=None, objects=None):
        history = history if history is not None else {}
        g = self._geo(W, H)

        faces = [f for f in (faces or []) if f[2] > 1 and f[3] > 1]
        main, minor = self._subjects(W, H, faces, objects)
        single = "face" if main and main[0].is_face else "subject"

        recent = history.get("dirs", [])
        in_bias = 0.5 if recent[-2:] == ["in", "in"] else (1.5 if recent[-2:] == ["out", "out"] else 1.0)

        if g.mode == "cover" and (g.tall or g.wide):
            kind = "pan_long"
        elif len(main) == 1:
            kind = self._pick(rng, {"push_" + single: 0.65 * in_bias, "pull_" + single: 0.35})
        elif len(main) >= 2:
            kind = self._pick(rng, {"group_in": 0.4 * in_bias, "group_out": 0.25,
                                    "drift": 0.35 if g.mode == "cover" else 0.0})
        else:
            kind = self._pick(rng, {"push_point": 0.45 * in_bias, "pull_point": 0.3,
                                    "lateral": 0.25 if g.mode == "cover" else 0.0})
        if history.get("kind") == kind and kind in ("drift", "lateral"):
            kind = {"drift": "group_in", "lateral": "push_point"}[kind]

        r0 = r1 = None
        if kind == "pan_long":
            r0, r1 = self._pan_long(g, main, minor, saliency, rng)
        elif kind in ("push_face", "pull_face", "push_subject", "pull_subject"):
            wide, tight = self._single(g, main[0], minor, rng)
            r0, r1 = (wide, tight) if kind.startswith("push") else (tight, wide)
        elif kind in ("group_in", "group_out"):
            wide, tight = self._group(g, main, minor, rng)
            r0, r1 = (wide, tight) if kind == "group_in" else (tight, wide)
        elif kind == "drift":
            res = self._drift(g, main, minor, rng)
            if res is None:
                kind = "group_in"
                wide, tight = self._group(g, main, minor, rng)
                r0, r1 = wide, tight
            else:
                r0, r1 = res
        elif kind in ("push_point", "pull_point"):
            wide, tight = self._point(g, faces, saliency, rng)
            r0, r1 = (wide, tight) if kind == "push_point" else (tight, wide)
        else:
            r0, r1 = self._lateral(g, saliency, rng)

        # Too little movement (photo already tightly framed, or low resolution)? Use a drift.
        if self._movement(g, r0, r1) < 0.05:
            if g.mode == "cover":
                alt = self._lateral(g, saliency, rng)
                if self._movement(g, *alt) > self._movement(g, r0, r1):
                    kind, (r0, r1) = "lateral", alt
            else:
                c = (W / 2, H / 2)
                if main:
                    c = main[0].anchor
                w_t = max(g.w_min, g.w_base / (1.0 + 0.12 * self.motion))
                alt = (g.place(c[0], c[1], g.w_base, 0.5, 0.4),
                       g.place(c[0], c[1], w_t, 0.5, 0.4))
                if rng.random() < 0.5:
                    alt = alt[::-1]
                if self._movement(g, *alt) > self._movement(g, r0, r1):
                    kind, (r0, r1) = "gentle_zoom", alt

        if self.wild:
            r0, r1 = self._extend(g, r0, r1, main, minor, self.style.get("extend", 0.0))
        direction = "in" if r1[2] < r0[2] * 0.995 else ("out" if r1[2] > r0[2] * 1.005 else "pan")
        history["dirs"] = (recent + [direction])[-4:]
        history["kind"] = kind
        keep = self._keep_boxes(g, main, r0, r1)
        r0, r1, bow, tilt = self._style(g, r0, r1, keep, straight_lines, rng)
        plan = self._finish(g, r0, r1, kind, faces, main, bow, tilt)
        plan["keep"] = [list(b) for b in keep]
        if self.wild:
            plan["ease"] = "punch"
            plan["punch"] = self.style["punch"]
        plan["objects"] = [list(o[:6]) for o in (objects or [])]
        return plan

    # ------------------------------------------------------------------ energetic styles
    def _extend(self, g, r0, r1, main, others, extend):
        """Wild / Hyperdrive: make the move travel further in its own direction (start a bit
        further back, end a bit further on), so it is faster. Only where the photo has room;
        a subject fully in view at an end stays fully in view there, no face gets newly cut by
        the frame edge, and the zoom change stays within MAX_MOVE_ZOOM."""
        if extend <= 0.0:
            return r0, r1

        def away(a, b, k):
            # a moved further away from b (centre and log-zoom), kept inside the photo
            w = math.exp(math.log(a[2]) + (math.log(a[2]) - math.log(b[2])) * k)
            return g.clamp_c(a[0] + (a[0] - b[0]) * k, a[1] + (a[1] - b[1]) * k, g.clamp_w(w))

        def cut(r, b):
            return 0.04 < g.overlap_frac(r, b) < 0.97

        for e in (extend, extend * 0.6, extend * 0.3):
            n0, n1 = away(r0, r1, e / 2), away(r1, r0, e / 2)
            if abs(math.log(n1[2] / n0[2])) > math.log(MAX_MOVE_ZOOM):
                continue
            ok = True
            for old, new in ((r0, n0), (r1, n1)):
                for s in main:
                    for box in (s.sb, s.tight):
                        if g.contains(old, box) and not g.contains(new, box):
                            ok = False
                for b in others:
                    if cut(new, b) and not cut(old, b):
                        ok = False
            if ok:
                return n0, n1
        return r0, r1

    # ------------------------------------------------------------------ curve and tilt
    def _keep_boxes(self, g, main, r0, r1):
        """Subjects that are fully in view at both ends of the move must stay fully in view
        (for a person's body: at least head and upper body)."""
        keep = []
        for s in main:
            for box in (s.sb, s.tight):
                if g.contains(r0, box) and g.contains(r1, box):
                    keep.append(box)
                    break
        return keep

    def _frame_ok(self, g, cx, cy, w, ang, keep):
        h = w / g.A
        tol = 0.5
        if g.mode == "cover":
            # the photo must fill the whole screen: every corner of the (turned) frame inside it
            for x, y in camera.frame_corners(cx, cy, w, h, ang):
                if x < -tol or y < -tol or x > g.W + tol or y > g.H + tol:
                    return False
        else:
            # whole photo on a blurred background: per direction, view inside photo or photo inside view
            for lo, ext, size in ((cx - w / 2, w, g.W), (cy - h / 2, h, g.H)):
                if ext <= size + tol:
                    if lo < -tol or lo + ext > size + tol:
                        return False
                elif lo > tol or lo + ext < size - tol:
                    return False
        return all(camera.box_in_frame(b, cx, cy, w, h, ang) for b in keep)

    def _path_ok(self, g, plan, keep, steps=24):
        for i in range(steps + 1):
            cx, cy, w, ang = camera.at(plan, i / steps)
            if not self._frame_ok(g, cx, cy, w, ang, keep):
                return False
        return True

    def _fit_turned(self, g, r, ang):
        """Move (and if needed zoom in a little on) rectangle r so that, turned by ang, the
        frame still lies completely inside the photo."""
        cx, cy, w = r
        c, s = abs(math.cos(ang)), abs(math.sin(ang))
        for _ in range(80):
            h = w / g.A
            ex, ey = (w * c + h * s) / 2, (w * s + h * c) / 2
            if 2 * ex <= g.W and 2 * ey <= g.H:
                return (min(max(cx, ex), g.W - ex), min(max(cy, ey), g.H - ey), w)
            w *= 0.997
        return None

    def _style(self, g, r0, r1, keep, straight_lines, rng):
        """Add a slow tilt and/or a gently curved path, but only where they fit. Every option
        is checked along the whole move; if it does not fit it is made smaller, or left out."""
        base = {"r0": r0, "r1": r1}
        tilt = None
        if self.tilt and g.mode == "cover" and (self.wild or (straight_lines or 0.0) < STRAIGHT_LINES_LIMIT):
            if self.wild:
                swing = rng.uniform(*self.style["tilt"])                 # degrees, start to end
            else:
                swing = rng.uniform(0.6, 1.0) * min(self.motion, 1.3)
            sign = 1 if rng.random() < 0.5 else -1
            while swing >= 0.25:
                a = math.radians(swing / 2)
                t0, t1 = self._fit_turned(g, r0, a), self._fit_turned(g, r1, a)
                if t0 and t1 and min(t0[2], t1[2]) >= g.w_min * 0.999:
                    # turns slowly THROUGH level: slightly one way at the start, the other at the end
                    cand = {"r0": t0, "r1": t1, "tilt": (-sign * swing / 2, sign * swing / 2)}
                    if self._path_ok(g, cand, keep):
                        base, tilt = cand, cand["tilt"]
                        break
                swing *= 0.5
        bow = None
        if self.curved:
            a0, a1 = base["r0"], base["r1"]
            dx, dy = a1[0] - a0[0], a1[1] - a0[1]
            n = math.hypot(dx, dy)
            # How fast the camera travels across the picture (frame widths per whole move).
            # The bend must stay small next to that: a sideways bulge on a move that hardly
            # travels (mostly a zoom) looks like the pan going one way and then turning back.
            # With the bend at most a fifth of the travel, the direction changes by at most
            # about 30 degrees each way: a gentle arc that never turns back.
            p0 = camera.interp(a0, a1, 0.45, k=0.0)
            p1 = camera.interp(a0, a1, 0.55, k=0.0)
            travel = math.hypot(p1[0] - p0[0], p1[1] - p0[1]) / ((p0[2] + p1[2]) / 2) / 0.1
            want = min(rng.uniform(0.02, 0.04) * self.motion,  # bend: 2-4% of the frame width...
                       BOW_PER_TRAVEL * travel)                # ...but never big next to the travel
            dirs = []
            if n > 0 and want >= 0.004:
                dirs = [(-dy / n, dx / n), (dy / n, -dx / n)]  # sideways to the travel, either side
                if rng.random() < 0.5:
                    dirs.reverse()
            best = None
            for vx, vy in dirs:
                if g.lock == "x":
                    vx = 0.0
                if g.lock == "y":
                    vy = 0.0
                if not (vx or vy):
                    continue
                mag = want
                while mag >= 0.004 and (best is None or mag > best[0]):
                    cand = dict(base, bow=(vx * mag, vy * mag))
                    if self._path_ok(g, cand, keep):
                        best = (mag, cand["bow"])
                        break
                    mag *= 0.7
                if best is not None and best[0] >= 0.7 * want:
                    break                                 # good enough: keep the first nice fit
            if best is not None:
                bow = best[1]
        return base["r0"], base["r1"], bow, tilt

    @staticmethod
    def _movement(g, r0, r1):
        """Rough size of the move, relative to the frame (zoom + pan)."""
        w = min(r0[2], r1[2])
        pan = math.hypot(r1[0] - r0[0], (r1[1] - r0[1]) * g.A) / w
        return camera.log_zoom_ratio(r0, r1) + pan

    # ------------------------------------------------------------------ shot types
    def _single(self, g, s, others, rng):
        """One subject: start wide and push in (or the reverse)."""
        sb, anchor = s.sb, s.anchor
        zr = self._zr(rng)
        w_wide = g.clamp_w(g.w_base / rng.uniform(1.0, 1.03))
        if s.is_face:
            tf = rng.uniform(0.2, 0.3)                # face height as part of frame height
            w_tight = s.size * g.A / tf
        else:
            # a pet, or a person's head and upper body, filling a good part of the frame
            w_tight = g.fit_width(s.tight, rng.uniform(0.6, 0.75), rng.uniform(0.65, 0.8))
        w_tight = max(w_tight, w_wide / zr)
        if w_tight > w_wide / 1.08:
            # subject is already big in the picture: still give it a small, gentle push
            w_tight = w_wide / (1.0 + 0.1 * self.motion)
        w_tight = g.clamp_w(w_tight)
        fx = self._fx(g, anchor[0])
        tight = g.frame_box(s.tight, w_tight, fx, 0.40, anchor)
        wide = g.frame_box(sb, w_wide, fx, 0.42, anchor)
        tight = g.resolve_cuts(tight, [s.tight], others, fx, 0.40, anchor)
        wide = g.resolve_cuts(wide, [sb], others, fx, 0.42, anchor)
        return wide, tight

    def _group(self, g, main, others, rng):
        """Several subjects: frame them all and slowly tighten (or the reverse)."""
        gb = _union([s.sb for s in main])
        gt = _union([s.tight for s in main])
        anchor = ((gb[0] + gb[2]) / 2, sum(s.anchor[1] for s in main) / len(main))
        zr = self._zr(rng)
        w_wide = g.clamp_w(g.w_base / rng.uniform(1.0, 1.03))
        w_tight = max(g.fit_width(gt, 0.78, 0.78), w_wide / zr)
        if w_tight > w_wide / 1.08:
            w_tight = w_wide / (1.0 + 0.1 * self.motion)
        w_tight = g.clamp_w(w_tight)
        fx = self._fx(g, anchor[0], 0.6)
        tight = g.frame_box(gt, w_tight, fx, 0.42, anchor)
        wide = g.frame_box(gb, w_wide, fx, 0.45, anchor)
        tight = g.resolve_cuts(tight, [gt], others, fx, 0.42, anchor)
        return wide, tight

    def _drift(self, g, main, others, rng):
        """Drift from one subject to another (the left-most and the right-most)."""
        a, b = main[0], main[-1]
        sa, aa, sbx, ab = a.tight, a.anchor, b.tight, b.anchor
        dx = abs(ab[0] - aa[0])
        w_d = max(g.fit_width(sa, 0.5, 0.6), g.fit_width(sbx, 0.5, 0.6), dx / 0.7,
                  g.w_base / (1.0 + 0.45 * self.motion))
        if w_d > g.w_base / 1.08 or dx < 0.12 * g.W:
            return None
        w_d = g.clamp_w(w_d)
        others_all = [s.tight for s in main] + list(others)
        ra = g.frame_box(sa, w_d * rng.uniform(1.0, 1.07), 0.5, 0.42, aa)
        rb = g.frame_box(sbx, w_d, 0.5, 0.42, ab)
        ra = g.resolve_cuts(ra, [sa], [o for o in others_all if o != sa], 0.5, 0.42, aa)
        rb = g.resolve_cuts(rb, [sbx], [o for o in others_all if o != sbx], 0.5, 0.42, ab)
        if self._movement(g, ra, rb) < 0.08:
            return None
        return (ra, rb) if rng.random() < 0.5 else (rb, ra)

    def _interest(self, g, faces, saliency, rng):
        if faces:   # only tiny faces: aim at them as a group
            boxes = [_face_boxes(f, g.W, g.H)[0] for f in faces]
            u = _union(boxes)
            return ((u[0] + u[2]) / 2, (u[1] + u[3]) / 2)
        if saliency and saliency[2] >= 1.6:
            # do not trust saliency all the way to the edges
            return (g.W * (0.5 + 0.8 * (saliency[0] - 0.5)), g.H * (0.5 + 0.8 * (saliency[1] - 0.5)))
        return (g.W * rng.uniform(0.42, 0.58), g.H * rng.uniform(0.42, 0.55))

    def _point(self, g, faces, saliency, rng):
        p = self._interest(g, faces, saliency, rng)
        zr = self._zr(rng)
        w_wide = g.clamp_w(g.w_base / rng.uniform(1.0, 1.03))
        w_tight = g.clamp_w(w_wide / zr)
        fx, fy = self._fx(g, p[0]), self._fy(g, p[1])
        return g.place(p[0], p[1], w_wide, fx, fy), g.place(p[0], p[1], w_tight, fx, fy)

    def _lateral(self, g, saliency, rng):
        """Slow sideways (or up/down) drift at a slight zoom, with a whisper of zoom change."""
        w = g.clamp_w(g.w_base / rng.uniform(1.07, 1.07 + 0.08 * self.motion))
        h = w / g.A
        room_x, room_y = g.W - w, g.H - h
        cx, cy = g.W / 2, g.H / 2
        if saliency and saliency[2] >= 1.6:
            cx = g.W * (0.5 + 0.5 * (saliency[0] - 0.5))
            cy = g.H * (0.5 + 0.5 * (saliency[1] - 0.5))
        sign = 1 if rng.random() < 0.5 else -1
        w1 = g.clamp_w(w * rng.uniform(0.97, 1.03))
        if room_x / w >= room_y / h:
            t = min(room_x, 0.16 * w * self.motion) / 2
            r0 = g.clamp_c(cx - sign * t, cy, w)
            r1 = g.clamp_c(cx + sign * t, cy, w1)
        else:
            t = min(room_y, 0.16 * h * self.motion) / 2
            r0 = g.clamp_c(cx, cy - sign * t, w)
            r1 = g.clamp_c(cx, cy + sign * t, w1)
        return r0, r1

    def _pan_long(self, g, main, minor, saliency, rng):
        """Tall photo (pan up/down) or panorama (pan sideways) when shown filling the screen."""
        w0 = g.clamp_w(g.w_base)
        w1 = g.clamp_w(g.w_base / rng.uniform(1.0, 1.0 + 0.08 * self.motion))
        vertical = g.tall
        frame_len = (w0 / g.A) if vertical else w0
        travel = rng.uniform(0.7, 1.1) * frame_len * self.motion
        if main:
            gb = _union([s.tight for s in main])
            anchor = ((gb[0] + gb[2]) / 2, sum(s.anchor[1] for s in main) / len(main))
            fx = self._fx(g, anchor[0], 0.6) if vertical else 0.5
            on_face = g.frame_box(gb, w1, fx, 0.40, anchor)
            if vertical:
                # start from the side with more room, so we travel towards the faces
                below = g.H - on_face[1]
                d = travel if below >= on_face[1] else -travel
                away = g.clamp_c(on_face[0], on_face[1] + d, w0)
            else:
                right = g.W - on_face[0]
                d = travel if right >= on_face[0] else -travel
                away = g.clamp_c(on_face[0] + d, on_face[1], w0)
            return (away, on_face) if rng.random() < 0.65 else (on_face, away)
        mid = (g.W / 2, g.H / 2)
        if saliency and saliency[2] >= 1.6:
            mid = (g.W * saliency[0], g.H * saliency[1])
        sign = 1 if rng.random() < 0.5 else -1
        if vertical:
            r0 = g.clamp_c(g.W / 2, mid[1] - sign * travel / 2, w0)
            r1 = g.clamp_c(g.W / 2, mid[1] + sign * travel / 2, w1)
        else:
            r0 = g.clamp_c(mid[0] - sign * travel / 2, g.H / 2, w0)
            r1 = g.clamp_c(mid[0] + sign * travel / 2, g.H / 2, w1)
        return r0, r1

    # ------------------------------------------------------------------ texture
    def _finish(self, g, r0, r1, kind, faces, main, bow=None, tilt=None):
        w_tight = min(r0[2], r1[2])
        mag = self.SW / w_tight                 # screen pixels per photo pixel at the closest point
        ts = min(1.0, mag)                      # texture scale: 1 texel = 1 screen pixel when closest
        if g.mode == "contain":
            region = (0.0, 0.0, float(g.W), float(g.H))
        else:
            # the part of the photo the camera ever sees (follows the curve and the tilt)
            path = {"r0": r0, "r1": r1, "bow": bow, "tilt": tilt}
            xs, ys = [], []
            for i in range(41):
                cx, cy, w, ang = camera.at(path, i / 40)
                for x, y in camera.frame_corners(cx, cy, w, w / g.A, ang):
                    xs.append(x)
                    ys.append(y)
            pad = 4.0 / ts
            region = (max(0.0, min(xs) - pad), max(0.0, min(ys) - pad),
                      min(float(g.W), max(xs) + pad), min(float(g.H), max(ys) + pad))
        rw, rh = region[2] - region[0], region[3] - region[1]
        tw, th = rw * ts, rh * ts
        big = max(tw, th)
        k = MAX_TEX / big
        if self.max_tex:
            k = min(k, self.max_tex[0] / tw, self.max_tex[1] / th)
        k = min(k, 1.0)
        tw, th = max(2, int(round(tw * k))), max(2, int(round(th * k)))
        return {
            "W": g.W, "H": g.H, "mode": g.mode, "lock": g.lock, "w_base": g.w_base,
            "r0": tuple(r0), "r1": tuple(r1), "kind": kind,
            "region": region, "tex": (tw, th),
            # texture pixels enlarged on screen at the closest point: smoother enlarging filter
            "bicubic": mag / (ts * k) > 1.03,
            "low_res": g.low_res,
            "bow": tuple(bow) if bow else None,
            "tilt": tuple(tilt) if tilt else None,
            "faces": [list(map(float, f[:5])) for f in faces],
            "main_faces": sum(1 for m in main if m.is_face),
            "main_subjects": [m.kind for m in main],
        }
