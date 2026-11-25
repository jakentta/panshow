"""Test the "by date taken" order and "continue where the last slideshow stopped".
Uses a throw-away folder and throw-away APPDATA / LOCALAPPDATA. Usage: test_order.py <scratch folder>"""
import datetime
import os
import shutil
import sys
import time

scratch = os.path.abspath(sys.argv[1])
shutil.rmtree(scratch, ignore_errors=True)
os.makedirs(scratch)
os.environ["APPDATA"] = os.path.join(scratch, "Roaming")
os.environ["LOCALAPPDATA"] = os.path.join(scratch, "Local")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from panshow import config, imaging, worker  # noqa: E402

ok = True


def check(what, got, want):
    global ok
    good = got == want
    ok &= good
    print("%-62s %s" % (what, "OK" if good else "WRONG: got %r, want %r" % (got, want)))


def ts(s):
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()


# 1. dates in file names
check("Pixel name PXL_20241205_094315319.jpg", imaging.name_date("PXL_20241205_094315319.jpg"), ts("2024-12-05 09:43:15"))
check("screenshot name with spaces", imaging.name_date("Screenshot 2025-03-12 112913.png"), ts("2025-03-12 11:29:13"))
check("IMG-20230101-WA0003.jpg (date only)", imaging.name_date("IMG-20230101-WA0003.jpg"), ts("2023-01-01 00:00:00"))
check("a long number is not a date (1773269116009.png)", imaging.name_date("1773269116009.png"), None)
check("crw_5749.jpg has no date", imaging.name_date("crw_5749.jpg"), None)
check("impossible date 20231399", imaging.name_date("x_20231399.jpg"), None)

# 2. a folder where file dates, camera dates and name dates disagree
photos = os.path.join(scratch, "photos")
os.makedirs(photos)
made = [  # name, camera date (EXIF) or None
    ("b_late.jpg", "2010:06:01 12:00:00"),
    ("a_early.jpg", "2005:11:16 11:05:37"),
    ("PXL_20080305_101010000.jpg", None),          # no camera date: the name says 2008
    ("c_middle.jpg", "2007:01:02 03:04:05"),
    ("z_nodate.jpg", None),                        # nothing: the file's own date (newest)
]
now = time.time()
for k, (name, exif) in enumerate(made):
    im = Image.effect_noise((320, 240), 60).convert("RGB")       # (Panshow skips files under 4 KB)
    ex = Image.Exif()
    if exif:
        ex.get_ifd(0x8769)[0x9003] = exif
    im.save(os.path.join(photos, name), exif=ex.tobytes(), quality=80)
    os.utime(os.path.join(photos, name), (now - 1000 * k, now - 1000 * k))   # file dates in reverse
check("camera date read from the photo", imaging.exif_date(os.path.join(photos, "a_early.jpg")),
      ts("2005-11-16 11:05:37"))
check("an online-only file is not opened (name/file date only)",
      imaging.photo_date(os.path.join(photos, "a_early.jpg"), "a_early.jpg", 123.0, True), 123.0)

want = ["a_early.jpg", "c_middle.jpg", "pxl_20080305_101010000.jpg", "b_late.jpg", "z_nodate.jpg"]  # (lower case)
s = config.Settings(folder=photos, order="date", resume=False).validated()
w = worker.Worker(s, 640, 360)
check("date order follows when the photos were taken", [fe[1] for fe in w.order.files], want)
check("starts from the oldest", w.order.next()[1], "a_early.jpg")
second = w.order.next()
third = w.order.next()                     # prepared in advance, never shown
w.order.shown(w.order.files[0])
w.order.shown(second)
w.close()

# 3. continue where it stopped: after the last photo SHOWN (c_middle), not the last prepared
s2 = config.Settings(folder=photos, order="date", resume=True).validated()
w2 = worker.Worker(s2, 640, 360)
check("continue: next is the one after the last shown", w2.order.next()[1], third[1])
w2.close()
w3 = worker.Worker(s, 640, 360)
check("continue switched off: starts from the oldest again", w3.order.next()[1], "a_early.jpg")
# a pair counts as its later photo
w3.order.shown(("pair", w3.order.files[3], w3.order.files[1]))
w3.close()
w4 = worker.Worker(s2, 640, 360)
check("after a pair: continues after its later photo", w4.order.next()[1], "z_nodate.jpg")
# the background re-scan must not jump back to the start
w4.order.next()
w4.fresh = (list(w4.order.files), dict(w4.dates))
w4.order = worker.Order(list(w4.order.files), "date", w4.rng, w4.pos_path, w4.dates)
w4.order.i = 2                                  # pretend two photos were taken from the old list
w4.produce()                                  # swaps in the fresh list (and prepares a photo)
check("after the background re-scan it carries on", w4.order.files[w4.order.i - 1][1], want[2])
w4.close()

# 4. stop after the last photo
s3 = config.Settings(folder=photos, order="date", stop_at_end=True).validated()
w5 = worker.Worker(s3, 640, 360)
names = []
for _ in range(8):
    item = w5.produce()
    if item.get("end"):
        names.append("END")
        break
    names.append(worker.fe_name(item["fe"]))
check("stop after the last: every photo once, then the end", names, want + ["END"])
check("...and it stays at the end", bool(w5.produce().get("end")), True)
w5.close()
s4 = config.Settings(folder=photos, order="date", stop_at_end=False).validated()
w6 = worker.Worker(s4, 640, 360)
after = [worker.fe_name(w6.produce()["fe"]) for _ in range(len(want) + 1)]
check("without it: starts again from the beginning", after[-1], want[0])
w6.close()
# with two tall photos side by side, a tall photo still waiting at the end is shown alone
for k in range(2):
    im = Image.effect_noise((240, 320), 60).convert("RGB")
    ex = Image.Exif()
    ex.get_ifd(0x8769)[0x9003] = "2011:01:0%d 10:00:00" % (k + 1)
    im.save(os.path.join(photos, "tall%d.jpg" % k), exif=ex.tobytes(), quality=80)
im = Image.effect_noise((240, 320), 60).convert("RGB")
ex = Image.Exif()
ex.get_ifd(0x8769)[0x9003] = "2012:01:01 10:00:00"
im.save(os.path.join(photos, "tall_last.jpg"), exif=ex.tobytes(), quality=80)
s5 = config.Settings(folder=photos, order="date", stop_at_end=True, tall_photo_style="pair").validated()
w7 = worker.Worker(s5, 640, 360)
names = []
for _ in range(12):
    item = w7.produce()
    if item.get("end"):
        names.append("END")
        break
    names.append(worker.fe_name(item["fe"]))
#   (a tall photo waits for a partner, so the next wide photo goes first; with none coming,
#    the tall one is shown alone before the end)
check("pairs + stop at the end: nothing lost, tall leftover shown alone",
      names, ["a_early.jpg", "c_middle.jpg", "pxl_20080305_101010000.jpg", "b_late.jpg",
              "tall0.jpg + tall1.jpg", "z_nodate.jpg", "tall_last.jpg", "END"])
w7.close()
print("ALL PASSED" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
