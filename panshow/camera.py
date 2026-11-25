"""Camera path maths, shared by the planner (worker process) and the renderer.

A camera "rectangle" is (cx, cy, w): the centre and width of the visible part of the photo,
measured in photo pixels. Its height is always w / screen_aspect.

Between the start and end rectangle the camera moves as one smooth "similarity" motion:
the zoom changes at a constant rate on a logarithmic scale (so it looks evenly paced to the
eye) and every point of the picture travels in a straight line. A handy property: each edge
of the visible rectangle moves steadily from its start value to its end value, so anything
that is inside both the start and end frames stays inside the frame the whole time.
"""
import math

EASE_K = 0.6


def ease(u, k=EASE_K):
    """Soft S-curve. k=0 is linear, k=1 is a full smoothstep that stops at the ends.

    We use a blend so the photo is always gently moving: it starts and ends at 40% of its
    average speed (while it is fading in / out) and peaks at 130% in the middle.
    """
    if u <= 0.0:
        return 0.0
    if u >= 1.0:
        return 1.0
    s = u * u * (3.0 - 2.0 * u)
    return u + k * (s - u)


def punch(u, a=0.6):
    """Timing curve for wild moves: starts fast (1.6x the average speed, right at the cut) and
    slows down towards the end (0.4x) without ever stopping."""
    if u <= 0.0:
        return 0.0
    if u >= 1.0:
        return 1.0
    return u + a * ((1.0 - (1.0 - u) ** 2) - u)


def interp(r0, r1, u, k=EASE_K):
    cx0, cy0, w0 = r0
    cx1, cy1, w1 = r1
    p = ease(u, k)
    r = w1 / w0
    if abs(r - 1.0) < 1e-6:
        s = 1.0 + p * (r - 1.0)
        q = p
    else:
        s = r ** p
        q = (1.0 - s) / (1.0 - r)
    a = s - r * q
    return cx0 * a + cx1 * q, cy0 * a + cy1 * q, w0 * s


def at(plan, u, k=EASE_K):
    """Full camera at time u (0..1): (cx, cy, w, angle_in_radians).

    On top of the straight move, a plan may have:
      "bow":  (bx, by) - the path bends sideways into a gentle arc; at the middle of the move
              the camera is bx*w, by*w photo pixels away from the straight line.
      "tilt": (a0, a1) - the picture turns slowly from a0 to a1 degrees.
    Both follow the same S-curve as the move itself, so they start and stop softly with it.
    """
    if plan.get("ease") == "punch":
        p = punch(u, plan.get("punch", 0.6))
        cx, cy, w = interp(plan["r0"], plan["r1"], p, 0.0)   # (k=0: straight through p)
    else:
        cx, cy, w = interp(plan["r0"], plan["r1"], u, k)
        p = ease(u, k)
    bow = plan.get("bow")
    if bow:
        s = math.sin(math.pi * p) * w
        cx += bow[0] * s
        cy += bow[1] * s
    tilt = plan.get("tilt")
    ang = math.radians(tilt[0] + (tilt[1] - tilt[0]) * p) if tilt else 0.0
    return cx, cy, w, ang


def frame_corners(cx, cy, w, h, ang):
    """The four corners of the (possibly turned) visible frame, in photo pixels."""
    c, s = math.cos(ang), math.sin(ang)
    out = []
    for qx, qy in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)):
        x, y = qx * w, qy * h
        out.append((cx + x * c - y * s, cy + x * s + y * c))
    return out


def box_in_frame(box, cx, cy, w, h, ang, tol=0.5):
    """Is the axis-aligned box (x0, y0, x1, y1) completely inside the turned frame?"""
    c, s = math.cos(ang), math.sin(ang)
    for x, y in ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])):
        dx, dy = x - cx, y - cy
        fx, fy = dx * c + dy * s, -dx * s + dy * c     # turn back into the frame's own directions
        if abs(fx) > w / 2 + tol or abs(fy) > h / 2 + tol:
            return False
    return True


def smoothstep(x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    return x * x * (3.0 - 2.0 * x)


def log_zoom_ratio(r0, r1):
    return abs(math.log(r1[2] / r0[2]))
