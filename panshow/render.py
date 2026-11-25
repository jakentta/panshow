"""GPU drawing: one full-screen pass per photo, with high quality texture filtering."""
import logging
import math

import moderngl

from . import camera

SRGB8_ALPHA8 = 0x8C43

VERT = """
#version 330
void main() {
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

# Notes on quality:
# * Textures are sRGB, so the GPU filters in linear light; we convert back to sRGB at the end.
# * The photo was pre-scaled so that one texel is never smaller than ~1/2 screen pixel.
#   For shrinking we take 4 bilinear samples in a rotated grid (cheap supersampling), which
#   keeps fine detail stable while the image creeps along by fractions of a pixel.
# * For low resolution photos (enlarged on screen) we use a Catmull-Rom bicubic filter.
# * A tiny static dither prevents banding in skies during crossfades.
FRAG = """
#version 330
uniform sampler2D u_tex;
uniform sampler2D u_bg;
uniform vec2 u_screen;      // size of the area drawn into (the whole screen, or one half of it)
uniform vec2 u_origin;      // bottom-left corner of that area
uniform vec2 u_center;     // photo position (0..1 across its part of the texture) at the screen centre
uniform vec4 u_axes;        // how that position changes across the screen: xy = left->right, zw = top->bottom
uniform vec4 u_bgmap;
uniform vec2 u_texsize;     // full size of the (re-used) texture
uniform vec2 u_texscale;    // part of the texture that holds this photo
uniform vec4 u_uvclamp;     // keep samples inside that part
uniform float u_alpha;
uniform int u_bicubic;
uniform int u_usebg;
out vec4 frag;

vec3 lin2srgb(vec3 c) {
    c = clamp(c, 0.0, 1.0);
    return mix(c * 12.92, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, step(0.0031308, c));
}

float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

vec3 catmull_rom(vec2 uv) {
    vec2 sp = uv * u_texsize;
    vec2 t1 = floor(sp - 0.5) + 0.5;
    vec2 f = sp - t1;
    vec2 w0 = f * (-0.5 + f * (1.0 - 0.5 * f));
    vec2 w1 = 1.0 + f * f * (-2.5 + 1.5 * f);
    vec2 w2 = f * (0.5 + f * (2.0 - 1.5 * f));
    vec2 w3 = f * f * (-0.5 + 0.5 * f);
    vec2 w12 = w1 + w2;
    vec2 t0 = clamp((t1 - 1.0) / u_texsize, u_uvclamp.xy, u_uvclamp.zw);
    vec2 t3 = clamp((t1 + 2.0) / u_texsize, u_uvclamp.xy, u_uvclamp.zw);
    vec2 t12 = clamp((t1 + w2 / w12) / u_texsize, u_uvclamp.xy, u_uvclamp.zw);
    vec3 r = vec3(0.0);
    r += texture(u_tex, vec2(t0.x,  t0.y)).rgb  * w0.x  * w0.y;
    r += texture(u_tex, vec2(t12.x, t0.y)).rgb  * w12.x * w0.y;
    r += texture(u_tex, vec2(t3.x,  t0.y)).rgb  * w3.x  * w0.y;
    r += texture(u_tex, vec2(t0.x,  t12.y)).rgb * w0.x  * w12.y;
    r += texture(u_tex, vec2(t12.x, t12.y)).rgb * w12.x * w12.y;
    r += texture(u_tex, vec2(t3.x,  t12.y)).rgb * w3.x  * w12.y;
    r += texture(u_tex, vec2(t0.x,  t3.y)).rgb  * w0.x  * w3.y;
    r += texture(u_tex, vec2(t12.x, t3.y)).rgb  * w12.x * w3.y;
    r += texture(u_tex, vec2(t3.x,  t3.y)).rgb  * w3.x  * w3.y;
    return max(r, 0.0);
}

void main() {
    vec2 f = gl_FragCoord.xy - u_origin;
    vec2 p = vec2(f.x, u_screen.y - f.y) / u_screen;
    vec2 q = p - 0.5;
    // 0..1 across the photo's part of the texture (u_axes also carries the tilt, if any)
    vec2 uvp = u_center + u_axes.xy * q.x + u_axes.zw * q.y;
    vec2 dxp = u_axes.xy / u_screen.x;   // change per screen pixel to the right
    vec2 dyp = u_axes.zw / u_screen.y;   // change per screen pixel downwards
    vec2 uv = uvp * u_texscale;
    vec2 dx = dxp * u_texscale;
    vec2 dy = dyp * u_texscale;
    vec2 lo = u_uvclamp.xy;
    vec2 hi = u_uvclamp.zw;
    vec3 col;
    if (u_bicubic == 1) {
        col = catmull_rom(uv);
    } else {
        // 4 samples inside the screen pixel in a rotated grid (follows the tilt too)
        col = 0.25 * (texture(u_tex, clamp(uv + dx *  0.125 + dy * -0.375, lo, hi)).rgb +
                      texture(u_tex, clamp(uv + dx *  0.375 + dy *  0.125, lo, hi)).rgb +
                      texture(u_tex, clamp(uv + dx * -0.125 + dy *  0.375, lo, hi)).rgb +
                      texture(u_tex, clamp(uv + dx * -0.375 + dy * -0.125, lo, hi)).rgb);
    }
    if (u_usebg == 1) {
        // distance to the photo's edge in screen pixels (correct also when the photo is turned)
        vec2 perpx = vec2(length(vec2(dxp.x, dyp.x)), length(vec2(dxp.y, dyp.y)));
        vec2 inside = min(uvp, 1.0 - uvp) / perpx;
        vec2 cov2 = clamp(inside + 0.5, 0.0, 1.0);
        vec3 bg = texture(u_bg, u_bgmap.xy + p * u_bgmap.zw).rgb;
        float g = dot(bg, vec3(0.2126, 0.7152, 0.0722));
        bg = mix(vec3(g), bg, 0.75) * 0.38;               // darker and a little less colourful
        col = mix(bg, col, cov2.x * cov2.y);
    }
    vec3 c = lin2srgb(col);
    float n = (hash12(gl_FragCoord.xy) + hash12(gl_FragCoord.xy + 19.19) - 1.0) / 255.0;
    frag = vec4(c + n, u_alpha);
}
"""

SOLID_FRAG = """
#version 330
uniform float u_alpha;
out vec4 frag;
void main() { frag = vec4(0.0, 0.0, 0.0, u_alpha); }
"""


class TexturePool:
    """Re-uses big textures. Creating a new texture costs hidden GPU time the first time it is
    written (several ms for a 4K-sized photo), which can make a frame late. So a few are made
    at start-up, while the screen is still black, and photos are written into them again and
    again. If a photo ever needs a bigger one, it is created once and then kept."""

    def __init__(self, renderer, warm_size, warm_count=3, max_total=4, extra=None):
        """extra: (size, count) of a second kind of texture to make at start-up."""
        self.r = renderer
        self.free = []
        self.in_use = 0
        self.max_total = max_total
        for _ in range(warm_count):
            self.free.append(self._new(warm_size))
        if extra:
            for _ in range(extra[1]):
                self.free.append(self._new(extra[0]))

    def _new(self, size):
        w, h = (int(-(-int(v) // 256) * 256) for v in size)    # round up to a multiple of 256
        # write zeros now, so the graphics driver really sets the memory aside
        return self.r.make_texture((w, h), bytes(w * h * 4))

    def get(self, tw, th):
        fits = [t for t in self.free if t.width >= tw and t.height >= th]
        if fits:
            t = min(fits, key=lambda t: t.width * t.height)
            self.free.remove(t)
        else:
            while self.free and len(self.free) + self.in_use >= self.max_total:
                small = min(self.free, key=lambda x: x.width * x.height)
                self.free.remove(small)
                small.release()
            size = (tw * 1.1, th * 1.1)
            logging.info("new texture %dx%d for a %dx%d photo", int(size[0]), int(size[1]), tw, th)
            t = self._new(size)
        self.in_use += 1
        return t

    def put(self, t):
        self.in_use -= 1
        self.free.append(t)
        while len(self.free) + self.in_use > self.max_total:
            small = min(self.free, key=lambda x: x.width * x.height)
            self.free.remove(small)
            small.release()

    def release(self):
        for t in self.free:
            t.release()
        self.free = []


class Renderer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.prog = ctx.program(vertex_shader=VERT, fragment_shader=FRAG)
        self.vao = ctx.vertex_array(self.prog, [])
        self.u = {k: self.prog[k] for k in ("u_screen", "u_origin", "u_center", "u_axes", "u_bgmap", "u_texsize",
                                             "u_texscale", "u_uvclamp", "u_alpha", "u_bicubic", "u_usebg")}
        self.prog["u_tex"].value = 0
        self.prog["u_bg"].value = 1
        self.dummy = ctx.texture((1, 1), 4, data=bytes(4), internal_format=SRGB8_ALPHA8)
        self.solid = ctx.program(vertex_shader=VERT, fragment_shader=SOLID_FRAG)
        self.solid_vao = ctx.vertex_array(self.solid, [])

    def make_texture(self, size, data=None):
        t = self.ctx.texture(size, 4, data=data, internal_format=SRGB8_ALPHA8)
        t.filter = (moderngl.LINEAR, moderngl.LINEAR)
        t.repeat_x = False
        t.repeat_y = False
        return t

    def begin(self, width, height):
        ctx = self.ctx
        ctx.viewport = (0, 0, width, height)
        ctx.clear(0.0, 0.0, 0.0, 1.0)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        self.size = (width, height)

    def fill(self, area, alpha):
        """Darken an area (x, y, w, h) towards black."""
        if area[2] <= 0 or area[3] <= 0:
            return
        self.ctx.viewport = tuple(int(v) for v in area)
        self.solid["u_alpha"].value = float(alpha)
        self.solid_vao.render(moderngl.TRIANGLES, vertices=3)

    def draw(self, slide, cam, alpha, area=None):
        """slide: object with .plan, .tex, .bg, .tex_size;
        cam: (cx, cy, w) or (cx, cy, w, angle_in_radians), in photo pixels;
        area: (x, y, w, h) part of the screen to draw into (default: all of it)."""
        if area is None:
            area = (0, 0) + tuple(self.size)
        ax, ay, sw, sh = (int(v) for v in area)
        self.ctx.viewport = (ax, ay, sw, sh)
        A = sw / sh
        plan = slide.plan
        cx, cy, w = cam[:3]
        ang = cam[3] if len(cam) > 3 else 0.0
        h = w / A
        rx0, ry0, rx1, ry1 = plan["region"]
        rw, rh = rx1 - rx0, ry1 - ry0
        u = self.u
        u["u_screen"].value = (float(sw), float(sh))
        u["u_origin"].value = (float(ax), float(ay))
        c, s = math.cos(ang), math.sin(ang)
        u["u_center"].value = ((cx - rx0) / rw, (cy - ry0) / rh)
        # screen left->right and top->bottom, as steps across the photo (turned by the tilt)
        u["u_axes"].value = (w * c / rw, w * s / rh, -h * s / rw, h * c / rh)
        TW, TH = slide.tex.size
        tw, th = slide.tex_size
        u["u_texsize"].value = (float(TW), float(TH))
        u["u_texscale"].value = (tw / TW, th / TH)
        u["u_uvclamp"].value = (0.5 / TW, 0.5 / TH, (tw - 0.5) / TW, (th - 0.5) / TH)
        u["u_alpha"].value = float(alpha)
        u["u_bicubic"].value = 1 if plan["bicubic"] else 0
        slide.tex.use(0)
        if plan["mode"] == "contain" and slide.bg is not None:
            W, H = plan["W"], plan["H"]
            wb = min(W, H * A) / (1.06 * (plan["w_base"] / w) ** 0.3)
            hb = wb / A
            bcx = min(max(cx, wb / 2), W - wb / 2)
            bcy = min(max(cy, hb / 2), H - hb / 2)
            u["u_bgmap"].value = ((bcx - wb / 2) / W, (bcy - hb / 2) / H, wb / W, hb / H)
            u["u_usebg"].value = 1
            slide.bg.use(1)
        else:
            u["u_usebg"].value = 0
            self.dummy.use(1)
        self.vao.render(moderngl.TRIANGLES, vertices=3)

    def draw_at(self, slide, u_time, alpha, kick=0.0, twist=0.0, area=None):
        cx, cy, w, ang = camera.at(slide.plan, u_time)
        # kick: a brief extra zoom-in; twist: a brief extra turn (always covered by the kick)
        self.draw(slide, (cx, cy, w / (1.0 + kick), ang + twist), alpha, area)
