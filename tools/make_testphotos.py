"""Build a small set of test photos (faces at different places, tall, panorama, EXIF-rotated,
low resolution) from public images. Usage: make_testphotos.py <download_dir> <out_dir>"""
import glob
import os
import sys

from PIL import Image

dl, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
astro = Image.open(os.path.join(dl, "astronaut.png")).convert("RGB")
walls = sorted(glob.glob(r"C:\Windows\Web\Wallpaper\Theme*\*.jpg"))
bg = [Image.open(p).convert("RGB") for p in walls[:6]]
print("wallpaper sizes", [b.size for b in bg])


def paste(base, person, cx, cy, size):
    p = person.resize((size, size), Image.LANCZOS)
    base.paste(p, (int(cx - size / 2), int(cy - size / 2)))
    return base


big = bg[0].resize((5400, 3600), Image.LANCZOS)
paste(big.copy(), astro, 3900, 1500, 1500).save(os.path.join(out, "astro_right.jpg"), quality=92)
paste(big.copy(), astro, 350, 1600, 1500).save(os.path.join(out, "astro_edge.jpg"), quality=92)
two = paste(big.copy(), astro, 1300, 1700, 1300)
paste(two, astro.transpose(Image.FLIP_LEFT_RIGHT), 4200, 1900, 1150).save(os.path.join(out, "two_people.jpg"), quality=92)
small_face = paste(bg[1].resize((5400, 3600), Image.LANCZOS), astro, 2000, 2300, 380)
small_face.save(os.path.join(out, "small_face.jpg"), quality=92)

tall = bg[2].resize((3600, 2400), Image.LANCZOS).crop((600, 0, 3000, 2400)).resize((2700, 3600), Image.LANCZOS)
paste(tall, astro, 1350, 900, 1300).save(os.path.join(out, "tall_astro.jpg"), quality=92)

pano = Image.new("RGB", (bg[3].width + bg[4].width, max(bg[3].height, bg[4].height)))
pano.paste(bg[3], (0, 0))
pano.paste(bg[4], (bg[3].width, 0))
pano = pano.crop((0, int(pano.height * 0.2), pano.width, int(pano.height * 0.8)))
pano.save(os.path.join(out, "panorama.jpg"), quality=92)

up = paste(big.copy(), astro, 1700, 1400, 1400)
rot = up.transpose(Image.ROTATE_90)   # stored sideways ...
exif = Image.Exif()
exif[0x0112] = 6                       # ... and EXIF says "rotate 90 clockwise to view"
rot.save(os.path.join(out, "exif_rotated.jpg"), quality=92, exif=exif.tobytes())

scan = paste(bg[5].resize((960, 640), Image.LANCZOS), astro, 600, 300, 260)
scan.save(os.path.join(out, "old_scan_small.jpg"), quality=90)

Image.open(os.path.join(dl, "messi5.jpg")).save(os.path.join(out, "group_lowres.jpg"), quality=95)
bg[0].save(os.path.join(out, "landscape.jpg"), quality=92)
print("done:", sorted(os.listdir(out)))
