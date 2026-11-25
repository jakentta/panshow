"""Photo loading: EXIF rotation, colour profiles, high quality pre-scaling."""
import datetime
import io
import logging
import os
import re

from PIL import Image, ImageCms, ImageFilter, ImageOps

Image.MAX_IMAGE_PIXELS = 400_000_000   # allow big panoramas

try:
    # pi-heif: the read-only HEIC reader (LGPL licence, no GPL encoder inside)
    import pi_heif
    pi_heif.register_heif_opener()
    HEIF_OK = True
except Exception:          # pragma: no cover
    HEIF_OK = False

EXTENSIONS = {".jpg", ".jpeg", ".jpe", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
if HEIF_OK:
    EXTENSIONS |= {".heic", ".heif", ".hif"}

_SRGB = None


def _srgb():
    global _SRGB
    if _SRGB is None:
        _SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    return _SRGB


def exif_orientation(im):
    try:
        return int(im.getexif().get(0x0112, 1) or 1)
    except Exception:
        return 1


def oriented_size(im):
    w, h = im.size
    return (h, w) if exif_orientation(im) in (5, 6, 7, 8) else (w, h)


def open_image(path):
    im = Image.open(path)
    return im, oriented_size(im)


# a date in a file name, as phones and screenshot tools write it:
# PXL_20241205_094315319.jpg, IMG_20230101_120000.jpg, Screenshot 2025-03-12 112913.png
_NAME_DATE = re.compile(r"(?<!\d)((?:19|20)\d\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])"
                        r"(?:[ _T.-]?([01]\d|2[0-3])[-_.:]?([0-5]\d)(?:[-_.:]?([0-5]\d))?)?")


def _plausible(dt):
    return dt is not None and 1900 <= dt.year and dt <= datetime.datetime.now() + datetime.timedelta(days=2)


def name_date(name):
    """Date and time written in a file name (as a timestamp), or None."""
    for m in _NAME_DATE.finditer(os.path.basename(name)):
        try:
            dt = datetime.datetime(*(int(g) if g else 0 for g in m.groups()))
        except ValueError:
            continue
        if _plausible(dt):
            return dt.timestamp()
    return None


def exif_date(path):
    """When the photo was taken, from the camera's information inside it (reads only the start
    of the file), as a timestamp, or None."""
    try:
        with Image.open(path) as im:
            ex = im.getexif()
            value = ex.get_ifd(0x8769).get(0x9003) or ex.get(0x0132)   # DateTimeOriginal, else DateTime
        dt = datetime.datetime.strptime(str(value).strip()[:19], "%Y:%m:%d %H:%M:%S")
        return dt.timestamp() if _plausible(dt) else None
    except Exception:
        return None


def photo_date(path, name, mtime, cloud_only):
    """For the 'by date' order: when the photo was taken. From the photo's own information,
    else a date in the file name, else the file's date. Files kept only in the cloud
    (OneDrive) are never opened here: that would download them."""
    return (None if cloud_only else exif_date(path)) or name_date(name) or float(mtime)


def decode(im, want_size=None):
    """Decode (optionally at reduced size for JPEG), apply EXIF rotation.

    want_size: (w, h) in upright orientation; JPEG may then be decoded at 1/2, 1/4, 1/8 size
    as long as the result stays at least that big. Returns (image, icc_profile_bytes).
    """
    icc = im.info.get("icc_profile")
    if want_size and im.format == "JPEG":
        ww, wh = want_size
        if exif_orientation(im) in (5, 6, 7, 8):
            ww, wh = wh, ww
        try:
            im.draft(im.mode if im.mode in ("RGB", "L", "CMYK") else "RGB", (int(ww), int(wh)))
        except Exception:
            pass
    if getattr(im, "is_animated", False):
        im.seek(0)
    im.load()
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:
        logging.debug("exif_transpose failed", exc_info=True)
    im = _normalise_mode(im)
    return im, icc


def _normalise_mode(im):
    m = im.mode
    if m in ("RGB", "L", "CMYK"):
        return im
    if m in ("I;16", "I;16B", "I;16L", "I", "F"):
        import numpy as np
        a = np.asarray(im).astype("float32")
        hi = float(a.max()) or 1.0
        scale = 255.0 / (65535.0 if hi > 255 else max(hi, 1.0))
        return Image.fromarray((a * scale).clip(0, 255).astype("uint8"), "L")
    if m in ("RGBA", "LA", "PA") or (m == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        bg.alpha_composite(rgba)
        return bg.convert("RGB")
    return im.convert("RGB")


def to_srgb(im, icc):
    """Convert to plain sRGB RGB (phone photos are often Display P3)."""
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            desc = (ImageCms.getProfileDescription(src) or "").lower()
            if not (im.mode == "RGB" and "srgb" in desc):
                return ImageCms.profileToProfile(im, src, _srgb(), renderingIntent=0, outputMode="RGB")
        except Exception:
            logging.debug("colour profile conversion failed", exc_info=True)
    return im if im.mode == "RGB" else im.convert("RGB")


def make_texture(src, orig_size, region, tex_size, icc):
    """Cut `region` (in original-photo pixels, of a photo orig_size big) out of `src` (which
    may have been loaded smaller) and scale it to tex_size."""
    # (width and height are scaled separately: a JPEG loaded at half size rounds odd sizes up)
    sx, sy = src.width / orig_size[0], src.height / orig_size[1]
    box = (max(0.0, region[0] * sx), max(0.0, region[1] * sy),
           min(float(src.width), region[2] * sx), min(float(src.height), region[3] * sy))
    tw, th = tex_size
    shrink = (box[2] - box[0]) / tw
    out = src.resize((tw, th), Image.LANCZOS, box=box, reducing_gap=3.0 if shrink > 3 else None)
    out = to_srgb(out, icc)
    if shrink > 1.15:
        # tiny bit of crispness to make up for the GPU's own soft filtering
        out = out.filter(ImageFilter.UnsharpMask(radius=0.8, percent=40, threshold=1))
    return out


def make_background(src, icc, long_side=360):
    w, h = src.size
    s = long_side / max(w, h)
    small = src.resize((max(2, int(w * s)), max(2, int(h * s))), Image.BILINEAR, reducing_gap=2.0)
    small = to_srgb(small, icc)
    return small.filter(ImageFilter.GaussianBlur(radius=long_side / 36))


def detection_copy(src, long_side=1600):
    w, h = src.size
    s = long_side / max(w, h)
    rgb = src if src.mode == "RGB" else src.convert("RGB")
    if abs(s - 1.0) < 0.02:
        return rgb, 1.0
    return rgb.resize((max(1, int(round(w * s))), max(1, int(round(h * s)))), Image.BILINEAR,
                      reducing_gap=2.0 if s < 0.5 else None), s
