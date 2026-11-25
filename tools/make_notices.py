"""Write THIRD-PARTY-NOTICES.txt: the licences of every open-source part inside the program.
Usage: make_notices.py <output file>"""
import glob
import importlib.metadata as md
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIC = os.path.join(ROOT, "licenses")

# Python packages that end up inside the program (build-only tools are not listed)
BUNDLED = ["numpy", "opencv-python-headless", "pillow", "pi-heif", "moderngl", "glcontext", "glfw",
           "cryptography", "cffi", "soundfile", "sounddevice", "soundcard", "pyinstaller"]

LINE = "=" * 78


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read().strip()


def dist_licenses(name):
    d = md.distribution(name)
    meta = d.metadata
    lic = meta.get("License-Expression") or meta.get("License") or ""
    if not lic or len(lic) > 80:
        lic = "; ".join(c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License"))
    files = []
    for f in d.files or []:
        base = os.path.basename(str(f)).upper()
        if any(k in base for k in ("LICENSE", "LICENCE", "COPYING", "NOTICE")) and ".dist-info" in str(f):
            files.append(str(d.locate_file(f)))
    return d.version, lic, sorted(set(files))


def main(out):
    parts = [
        "PANSHOW - THIRD-PARTY NOTICES",
        "",
        "Panshow is built with the open-source components listed below. Each is used under its own",
        "licence, reproduced here. Panshow itself is free to use.",
        "",
    ]
    for name in BUNDLED:
        ver, lic, files = dist_licenses(name)
        parts += [LINE, "%s %s  -  licence: %s" % (name, ver, lic or "see below"), LINE]
        if name == "pyinstaller":
            parts.append("Only the PyInstaller start-up program (bootloader) is included. Its GPL licence has an "
                         "exception that allows it to be used in programs under any licence.")
        if name == "pi-heif":
            parts += ["The HEIC/HEIF photo reader includes the libraries libheif and libde265 (LGPL 3.0) as",
                      "separate, replaceable files (libheif-*.dll, libde265-*.dll in the _internal folder).",
                      "Source code: https://github.com/strukturag/libheif/tree/v1.18.1 and",
                      "             https://github.com/strukturag/libde265/tree/v1.0.15",
                      "The full LGPL 3.0 and GPL 3.0 texts are at the end of this file.", ""]
        for f in files:
            parts += ["--- %s ---" % os.path.basename(f), read(f), ""]

    py_lic = os.path.join(sys.base_prefix, "LICENSE.txt")
    parts += [LINE, "Python %s  -  licence: PSF License (includes the licences of parts Python bundles,"
              % sys.version.split()[0], "such as OpenSSL, zlib, libffi, Tcl/Tk)", LINE, read(py_lic), ""]
    for tcl in glob.glob(os.path.join(sys.base_prefix, "tcl", "t*8*", "license.terms"))[:1]:
        parts += [LINE, "Tcl/Tk 8.6 (settings window)  -  licence: Tcl/Tk licence, BSD-style (one text for both)",
                  LINE, read(tcl), ""]
    parts += [LINE, "GLFW (window and keyboard library)  -  licence: zlib", LINE,
              read(os.path.join(LIC, "glfw-LICENSE.txt")), ""]
    parts += [LINE, "YuNet face detection model (OpenCV Zoo)  -  licence: MIT", LINE,
              read(os.path.join(LIC, "yunet-model-LICENSE.txt")), ""]
    parts += [LINE, "YOLOX-S people and animal detection model (OpenCV Zoo, Megvii)  -  licence: Apache 2.0",
              LINE, read(os.path.join(LIC, "yolox-model-LICENSE.txt")), ""]
    parts += [LINE, "PortAudio (sound output, file libportaudio64bit.dll)  -  licence: PortAudio (MIT-style)",
              LINE, read(os.path.join(LIC, "portaudio-LICENSE.txt")), ""]
    sndfile_copying = os.path.join(sys.prefix, "Lib", "site-packages", "_soundfile_data", "COPYING")
    parts += [LINE, "libsndfile (reading MP3/FLAC/WAV/OGG, file libsndfile_x64.dll)  -  licence: LGPL 2.1", LINE,
              "A separate, replaceable library. Source code: https://github.com/libsndfile/libsndfile",
              "It contains these codecs: FLAC, Ogg, Vorbis, Opus (BSD-style, see below), and for MP3",
              "mpg123 (LGPL 2.1, https://www.mpg123.de) and LAME (LGPL, https://lame.sourceforge.io).", "",
              read(sndfile_copying) if os.path.exists(sndfile_copying) else read(os.path.join(LIC, "LGPL-2.1.txt")),
              ""]
    for name, f in (("libogg", "libogg-COPYING.txt"), ("libvorbis", "libvorbis-COPYING.txt"),
                    ("libFLAC", "libflac-COPYING.Xiph.txt"), ("Opus", "opus-COPYING.txt")):
        parts += [LINE, "%s (inside libsndfile)  -  licence: BSD-style" % name, LINE,
                  read(os.path.join(LIC, f)), ""]
    parts += [LINE, "libheif  -  licence: LGPL 3.0", LINE, read(os.path.join(LIC, "libheif-COPYING.txt")), ""]
    parts += [LINE, "libde265  -  licence: LGPL 3.0", LINE, read(os.path.join(LIC, "libde265-COPYING.txt")), ""]
    parts += [LINE, "GNU LESSER GENERAL PUBLIC LICENSE, version 3", LINE, read(os.path.join(LIC, "LGPL-3.0.txt")), ""]
    parts += [LINE, "GNU LESSER GENERAL PUBLIC LICENSE, version 2.1", LINE, read(os.path.join(LIC, "LGPL-2.1.txt")), ""]
    parts += [LINE, "GNU GENERAL PUBLIC LICENSE, version 3", LINE, read(os.path.join(LIC, "GPL-3.0.txt")), ""]
    with open(out, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(parts) + "\n")
    print("wrote %s (%d KB, %d components)" % (out, os.path.getsize(out) // 1024, len(BUNDLED) + 6))


if __name__ == "__main__":
    main(sys.argv[1])
