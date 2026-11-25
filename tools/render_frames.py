"""Render start / middle / end frames of each photo's camera move off-screen, for checking
composition and sharpness. Usage: render_frames.py <photo_folder> <out_dir> [blur|pan|pair] [seed]
[tilt|wild|hyperdrive]"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import moderngl  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from panshow import camera, config, planner, render, worker  # noqa: E402

SW, SH = 2560, 1600


class FakePart:
    pass


def main():
    folder, out = sys.argv[1], sys.argv[2]
    style = sys.argv[3] if len(sys.argv) > 3 else "blur"
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    os.makedirs(out, exist_ok=True)
    tilt = len(sys.argv) > 5 and sys.argv[5] in ("tilt", "wild", "hyperdrive")
    wild = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] in ("wild", "hyperdrive") else None
    s = config.Settings(folder=folder, tall_photo_style=style, order="name", curved_paths=True,
                        tilt=tilt, motion=wild or "normal").validated()
    w = worker.Worker(s, SW, SH, video=True)
    w.rng = random.Random(seed)
    ctx = moderngl.create_standalone_context(require=330)
    fbo = ctx.simple_framebuffer((SW, SH), components=4)
    fbo.use()
    r = render.Renderer(ctx)
    half, _gap = planner.pair_layout(SW)
    rows = []
    total = len(w.order.files)
    while len(w.shown) < total and len(rows) < total:
        item = w.produce()
        parts = []
        for k, (plan, tex) in enumerate(item["parts"]):
            p = FakePart()
            p.plan, p.tex_size = plan, tex.size
            p.tex = r.make_texture(tex.size, tex.convert("RGBA").tobytes())
            bg = item["bg"] if k == 0 else None
            p.bg = r.make_texture(bg.size, bg.convert("RGBA").tobytes()) if bg is not None else None
            parts.append(p)
        areas = [None] if len(parts) == 1 else [(0, 0, half, SH), (SW - half, 0, half, SH)]
        frames = []
        for u in (0.0, 0.5, 1.0):
            r.begin(SW, SH)
            for p, area in zip(parts, areas):
                r.draw_at(p, u, 1.0, area=area)
            img = Image.frombytes("RGBA", (SW, SH), fbo.read(components=4)).transpose(Image.FLIP_TOP_BOTTOM)
            name = worker.fe_name(item["fe"])
            if u == 1.0:
                img.convert("RGB").save(os.path.join(out, os.path.splitext(name.replace(" + ", "+"))[0]
                                                     + "_end_full.png"))
            d = ImageDraw.Draw(img)
            for p, area in zip(parts, areas):
                ax, _ay, aw, ah = area or (0, 0, SW, SH)
                cx, cy, cw, _ang = camera.at(p.plan, u)
                ch = cw * ah / aw
                for f in p.plan["faces"]:
                    x0 = ax + (f[0] - (cx - cw / 2)) / cw * aw
                    y0 = (f[1] - (cy - ch / 2)) / ch * ah
                    d.rectangle([x0, y0, x0 + f[2] / cw * aw, y0 + f[3] / ch * ah], outline=(255, 40, 40), width=6)
            for k in (1, 2):
                d.line([(SW * k / 3, 0), (SW * k / 3, SH)], fill=(255, 255, 255, 90), width=2)
                d.line([(0, SH * k / 3), (SW, SH * k / 3)], fill=(255, 255, 255, 90), width=2)
            frames.append(img.convert("RGB").resize((640, 400), Image.LANCZOS))
        row = Image.new("RGB", (640 * 3 + 20, 430), (40, 40, 40))
        for i, fimg in enumerate(frames):
            row.paste(fimg, (i * 650, 30))
        text = "   ".join("kind=%s mode=%s zoom %.2f->%.2f tilt=%s" % (
            p.plan["kind"], p.plan["mode"], p.plan["w_base"] / p.plan["r0"][2],
            p.plan["w_base"] / p.plan["r1"][2], p.plan["tilt"]) for p in parts)
        ImageDraw.Draw(row).text((6, 6), worker.fe_name(item["fe"]) + "  |  " + text, fill=(255, 255, 0))
        rows.append(row)
        for p in parts:
            p.tex.release()
            if p.bg:
                p.bg.release()
    w.close()
    sheet = Image.new("RGB", (rows[0].width, sum(rw.height for rw in rows)))
    y = 0
    for rw in rows:
        sheet.paste(rw, (0, y))
        y += rw.height
    path = os.path.join(out, "contact_%s_%d%s.jpg" % (style, seed, ("_" + wild) if wild else ("_tilt" if tilt else "")))
    sheet.save(path, quality=88)
    print("saved", path)


if __name__ == "__main__":
    main()
