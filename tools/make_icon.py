"""Draw the Panshow icon (a framed photo of hills with a small sun) as a multi-size .ico."""
import os
import sys

from PIL import Image, ImageDraw

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "panshow", "panshow.ico")
S = 256
im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle([8, 8, S - 8, S - 8], radius=44, fill=(28, 30, 38, 255))
inner = [36, 52, S - 36, S - 52]
sky = Image.new("RGBA", (inner[2] - inner[0], inner[3] - inner[1]))
sd = ImageDraw.Draw(sky)
for y in range(sky.height):
    t = y / sky.height
    sd.line([(0, y), (sky.width, y)], fill=(int(250 - 70 * t), int(170 + 20 * t), int(90 + 120 * t), 255))
sd.ellipse([sky.width * 0.62, sky.height * 0.14, sky.width * 0.82, sky.height * 0.40], fill=(255, 244, 214, 255))
sd.polygon([(0, sky.height), (0, sky.height * 0.62), (sky.width * 0.35, sky.height * 0.38),
            (sky.width * 0.7, sky.height * 0.72), (sky.width, sky.height * 0.55), (sky.width, sky.height)],
           fill=(40, 70, 90, 255))
im.paste(sky, (inner[0], inner[1]))
d.rectangle(inner, outline=(245, 245, 245, 255), width=10)
os.makedirs(os.path.dirname(out), exist_ok=True)
im.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("saved", out)
