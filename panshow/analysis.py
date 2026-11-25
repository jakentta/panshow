"""Face detection (OpenCV YuNet), a simple 'interesting area' guess, and the face cache."""
import hashlib
import json
import logging
import os
import sys
import time

import numpy as np

DET_VERSION = 1


def resource_path(*parts):
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    if getattr(sys, "_MEIPASS", None):
        return os.path.join(base, "panshow", *parts)
    return os.path.join(base, *parts)


class FaceDetector:
    def __init__(self):
        import cv2
        self.cv2 = cv2
        model = resource_path("models", "face_detection_yunet_2023mar.onnx")
        self.det = cv2.FaceDetectorYN.create(model, "", (320, 320), 0.55, 0.3, 5000)

    def detect(self, rgb_np):
        """rgb_np: HxWx3 uint8 RGB. Returns list of [x, y, w, h, score] in that image's pixels."""
        h, w = rgb_np.shape[:2]
        bgr = np.ascontiguousarray(rgb_np[:, :, ::-1])
        self.det.setInputSize((w, h))
        _, faces = self.det.detect(bgr)
        if faces is None:
            return []
        out = []
        short = min(w, h)
        for f in faces:
            x, y, fw, fh = (float(v) for v in f[:4])
            score = float(f[14])
            if fw < 10 or fh < 10:
                continue
            if not (0.45 < fw / fh < 1.7):
                continue
            # small faces must be confident; larger ones (often side-on) may be less certain
            if score < 0.75 and fh < 0.05 * short:
                continue
            x0, y0 = max(0.0, x), max(0.0, y)
            x1, y1 = min(float(w), x + fw), min(float(h), y + fh)
            if x1 - x0 < 8 or y1 - y0 < 8:
                continue
            out.append([x0, y0, x1 - x0, y1 - y0, score])
        return out


# COCO classes the camera cares about: people (whole body, also seen from behind) and animals
SUBJECT_CLASSES = {0: "person", 14: "bird", 15: "cat", 16: "dog", 17: "horse", 18: "sheep", 19: "cow",
                   20: "elephant", 21: "bear", 22: "zebra", 23: "giraffe"}


class ObjectDetector:
    """Finds people's bodies and animals (YOLOX-S from the OpenCV Model Zoo, Apache 2.0).
    Used so the camera can also aim at pets, and at people whose face is not visible."""

    SIZE = 640

    def __init__(self, model_name="object_detection_yolox_2022nov_int8.onnx"):
        import cv2
        self.cv2 = cv2
        self.net = cv2.dnn.readNet(resource_path("models", model_name))
        grids, strides = [], []
        for st in (8, 16, 32):
            n = self.SIZE // st
            xv, yv = np.meshgrid(np.arange(n), np.arange(n))
            grids.append(np.stack((xv, yv), 2).reshape(-1, 2))
            strides.append(np.full((n * n, 1), st))
        self.grids = np.concatenate(grids).astype(np.float32)
        self.strides = np.concatenate(strides).astype(np.float32)

    def detect(self, rgb_np, min_score=0.45):
        """rgb_np: HxWx3 uint8 RGB. Returns [x, y, w, h, score, class_id] in that image's pixels."""
        cv2 = self.cv2
        h, w = rgb_np.shape[:2]
        r = min(self.SIZE / h, self.SIZE / w)
        nh, nw = int(round(h * r)), int(round(w * r))
        pad = np.full((self.SIZE, self.SIZE, 3), 114.0, np.float32)
        pad[:nh, :nw] = cv2.resize(rgb_np, (nw, nh), interpolation=cv2.INTER_LINEAR)
        self.net.setInput(pad.transpose(2, 0, 1)[np.newaxis])
        out = self.net.forward(self.net.getUnconnectedOutLayersNames())[0][0]
        xy = (out[:, :2] + self.grids) * self.strides
        wh = np.exp(out[:, 2:4]) * self.strides
        scores = out[:, 4:5] * out[:, 5:]
        cls = np.argmax(scores, axis=1)
        best = scores[np.arange(len(cls)), cls]
        keep = np.isin(cls, list(SUBJECT_CLASSES)) & (best >= min_score)
        if not keep.any():
            return []
        xy, wh, best, cls = xy[keep], wh[keep], best[keep], cls[keep]
        boxes = np.concatenate([xy - wh / 2, wh], axis=1)
        idx = cv2.dnn.NMSBoxesBatched(boxes.tolist(), best.tolist(), cls.tolist(), min_score, 0.5)
        res = []
        for i in np.array(idx).reshape(-1):
            x, y, bw, bh = boxes[i] / r
            x0, y0 = max(0.0, x), max(0.0, y)
            x1, y1 = min(float(w), x + bw), min(float(h), y + bh)
            if x1 - x0 > 4 and y1 - y0 > 4:
                res.append([float(x0), float(y0), float(x1 - x0), float(y1 - y0), float(best[i]), int(cls[i])])
        return res


def saliency(gray_np):
    """'Spectral residual' saliency guess. Returns (x, y, strength) with x, y in 0..1."""
    import cv2
    h, w = gray_np.shape[:2]
    s = 96.0 / max(h, w)
    small = cv2.resize(gray_np, (max(8, int(w * s)), max(8, int(h * s))),
                       interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    f = np.fft.fft2(small)
    log_amp = np.log(np.abs(f) + 1e-8)
    phase = np.angle(f)
    resid = log_amp - cv2.blur(log_amp, (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(resid + 1j * phase))) ** 2
    sh, sw = sal.shape
    sal = cv2.GaussianBlur(sal.astype(np.float32), (0, 0), max(sh, sw) * 0.06)
    # stay away from the borders (FFT edge effects) and prefer the middle a little
    yy, xx = np.mgrid[0:sh, 0:sw]
    nx, ny = (xx + 0.5) / sw - 0.5, (yy + 0.5) / sh - 0.5
    sal *= 0.55 + 0.45 * np.exp(-(nx ** 2 + ny ** 2) / (2 * 0.3 ** 2))
    m = max(2, int(min(sh, sw) * 0.06))
    sal[:m, :] = 0
    sal[-m:, :] = 0
    sal[:, :m] = 0
    sal[:, -m:] = 0
    iy, ix = np.unravel_index(int(np.argmax(sal)), sal.shape)
    strength = float(sal[iy, ix] / (sal.mean() + 1e-9))
    return [(ix + 0.5) / sw, (iy + 0.5) / sh, round(strength, 3)]


def straight_lines(gray_np):
    """How much of the photo is made of long, level or upright lines (horizons, buildings,
    door frames). 0 = none; 1 = about one line across the whole photo. Used to never tilt
    such photos, because even a fraction of a degree makes them look crooked."""
    import cv2
    h, w = gray_np.shape[:2]
    s = 1000.0 / max(h, w)
    small = cv2.resize(gray_np, (max(16, int(w * s)), max(16, int(h * s))), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    edges = cv2.Canny(cv2.GaussianBlur(small, (3, 3), 0), 60, 160)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=60,
                            minLineLength=int(0.2 * min(sh, sw)), maxLineGap=6)
    score = 0.0
    if lines is not None:
        for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):    # (OpenCV 4 and 5 shapes differ)
            dx, dy = float(x2 - x1), float(y2 - y1)
            ang = abs(np.degrees(np.arctan2(dy, dx))) % 180.0
            if min(ang, 180.0 - ang) < 4.0:               # nearly level
                score += abs(dx) / sw
            elif abs(ang - 90.0) < 4.0:                   # nearly upright
                score += abs(dy) / sh
    return round(min(score, 9.99), 2)


def analyse(src, detector, object_detector=None):
    """Look at a decoded upright PIL image: faces, people's bodies and animals, the most
    'interesting' area and the straight-line check. Coordinates in src pixels.
    Returns a dict: faces, sal, lines, objects."""
    from . import imaging
    det_img, s = imaging.detection_copy(src)
    rgb = np.asarray(det_img)
    if rgb.ndim == 2:
        rgb = np.repeat(rgb[:, :, None], 3, axis=2)
    faces = detector.detect(rgb) if detector is not None else []
    faces = [[round(v / s, 1) for v in f[:4]] + [round(f[4], 3)] for f in faces]
    objects = []
    if object_detector is not None:
        objects = [[round(v / s, 1) for v in o[:4]] + [round(o[4], 3), int(o[5])]
                   for o in object_detector.detect(rgb)]
    gray = rgb.mean(axis=2).astype(np.uint8)
    return {"faces": faces, "sal": saliency(gray), "lines": straight_lines(gray), "objects": objects}


class FaceCache:
    """One small JSON file per photo folder, kept in %LOCALAPPDATA%\\Panshow\\cache."""

    def __init__(self, folder, cache_dir):
        key = hashlib.sha1(os.path.normcase(os.path.abspath(folder)).encode("utf-8")).hexdigest()[:16]
        self.path = os.path.join(cache_dir, "faces_%s.json" % key)
        self.folder = folder
        self.entries = {}
        self.dirty = 0
        self.last_save = time.monotonic()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") == DET_VERSION:
                self.entries = data.get("entries", {})
        except FileNotFoundError:
            pass
        except Exception:
            logging.exception("face cache unreadable, starting fresh")

    def get(self, rel, mtime, size):
        e = self.entries.get(rel)
        if e and e.get("m") == mtime and e.get("s") == size:
            return e
        return None

    def put(self, rel, mtime, size, W, H, found):
        """found: the dict from analyse()."""
        self.entries[rel] = dict(found, m=mtime, s=size, W=W, H=H)
        self.dirty += 1
        if self.dirty >= 5 or time.monotonic() - self.last_save > 8:
            self.save()

    def save(self):
        if not self.dirty:
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": DET_VERSION, "folder": self.folder, "entries": self.entries},
                          f, separators=(",", ":"))
            os.replace(tmp, self.path)
            self.dirty = 0
            self.last_save = time.monotonic()
        except Exception:
            logging.exception("could not save face cache")
