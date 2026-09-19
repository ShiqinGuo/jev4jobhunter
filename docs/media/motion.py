"""Small, deterministic vector-style motion renderer. Requires Pillow only."""

import math
import os
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, SCALE, FPS = 1120, 640, 2, 12


@lru_cache(None)
def font(size, bold=False):
    override = os.environ.get("SHOWCASE_FONT_BOLD" if bold else "SHOWCASE_FONT")
    candidates = [
        override,
        "C:/Windows/Fonts/seguisb.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return ImageFont.truetype(path, int(size * SCALE))
    raise RuntimeError(
        "Set SHOWCASE_FONT and SHOWCASE_FONT_BOLD to TrueType font paths"
    )


def ease(x):
    x = max(0, min(1, x))
    return x * x * (3 - 2 * x)


def spring(x):
    return 1 - math.exp(-6 * max(x, 0)) * math.cos(9 * max(x, 0))


def lerp(a, b, p):
    return a + (b - a) * p


@lru_cache(maxsize=48)
def shadow(w, h):
    layer = Image.new("RGBA", ((w + 50) * SCALE, (h + 50) * SCALE))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(
        (25 * SCALE, 25 * SCALE, (w + 25) * SCALE, (h + 25) * SCALE),
        radius=20 * SCALE,
        fill=(30, 40, 58, 24),
    )
    return layer.filter(ImageFilter.GaussianBlur(12))


class Canvas:
    def __init__(self, bg, ink, muted, accent, soft, edge):
        self.im = Image.new("RGB", (W * SCALE, H * SCALE), bg)
        self.d = ImageDraw.Draw(self.im)
        self.bg, self.ink, self.muted = bg, ink, muted
        self.accent, self.soft, self.edge = accent, soft, edge

    def rect(self, box, fill, radius=14, outline=None, width=1):
        self.d.rounded_rectangle(
            tuple(int(v * SCALE) for v in box),
            radius=int(radius * SCALE),
            fill=fill,
            outline=outline,
            width=int(width * SCALE),
        )

    def text(self, x, y, value, size=18, color=None, bold=False):
        self.d.text(
            (int(x * SCALE), int(y * SCALE)),
            value,
            font=font(size, bold),
            fill=color or self.ink,
        )

    def line(self, pts, color=None, width=2):
        self.d.line(
            [(int(x * SCALE), int(y * SCALE)) for x, y in pts],
            fill=color or self.edge,
            width=int(width * SCALE),
            joint="curve",
        )

    def circle(self, x, y, r, fill, outline=None, width=1):
        self.d.ellipse(
            tuple(int(v * SCALE) for v in (x - r, y - r, x + r, y + r)),
            fill=fill,
            outline=outline,
            width=int(width * SCALE),
        )

    def poly(self, pts, color):
        self.d.polygon([(int(x * SCALE), int(y * SCALE)) for x, y in pts], fill=color)

    def card(self, x, y, w, h, fill="#ffffff", edge=None, depth=7):
        shade = shadow(int(w), int(h))
        self.im.paste(shade, (int((x - 25) * SCALE), int((y - 13) * SCALE)), shade)
        self.d = ImageDraw.Draw(self.im)
        self.rect((x, y + depth, x + w, y + h + depth), edge or self.edge, 18)
        self.rect((x, y, x + w, y + h), fill, 18, edge or self.edge)

    def pill(self, x, y, value, active=False, size=15):
        w = self.d.textlength(value, font=font(size)) / SCALE + 24
        self.rect((x, y, x + w, y + 30), self.soft if active else self.bg, 10)
        self.text(x + 12, y + 4, value, size, self.accent if active else self.muted)
        return w

    def tick(self, x, y, color=None):
        self.line([(x - 7, y), (x - 2, y + 5), (x + 9, y - 7)], color or self.accent, 3)

    def packet(self, points, progress, color=None):
        color = color or self.accent
        lengths = [math.dist(a, b) for a, b in pairwise(points)]
        remaining = max(0, min(1, progress)) * sum(lengths)
        path = [points[0]]
        for i, length in enumerate(lengths):
            k = min(1, remaining / length) if length else 1
            a, b = points[i], points[i + 1]
            point = (lerp(a[0], b[0], k), lerp(a[1], b[1], k))
            path.append(point)
            remaining -= length
            if k < 1:
                break
        self.line(path, color, 3)
        x, y = path[-1]
        self.circle(x, y, 9, self.soft)
        self.circle(x, y, 4, color)

    def cube(self, x, y):
        self.poly(
            [(x, y - 13), (x + 19, y - 3), (x, y + 8), (x - 19, y - 3)], self.soft
        )
        self.poly(
            [(x - 19, y - 3), (x, y + 8), (x, y + 25), (x - 19, y + 14)], self.accent
        )
        self.poly(
            [(x, y + 8), (x + 19, y - 3), (x + 19, y + 14), (x, y + 25)], self.ink
        )

    def chrome(self, name, headline, number, caption, t):
        self.text(42, 25, name, 18, bold=True)
        self.pill(865, 23, "Illustrated workflow", size=14)
        self.text(42, 67, headline, 32, bold=True)
        self.text(44, 554, f"0{number}", 17, self.accent, True)
        self.text(84, 552, caption, 20, self.ink)
        self.line([(44, 607), (1076, 607)], self.edge, 2)
        self.line([(44, 607), (44 + 1032 * min(t / 24, 1), 607)], self.accent, 3)

    def end(self):
        return self.im.resize((W, H), Image.Resampling.LANCZOS)


def render(scene, out):
    """A 24s loop plus a brief final hold; global palette avoids palette shimmer."""
    out = Path(out)
    atlas = Image.new("RGB", (W * 3, H * 2))
    snapshots = [2.5, 6.5, 10.5, 14.5, 18.5, 23.5]
    for i, t in enumerate(snapshots):
        atlas.paste(scene(t), ((i % 3) * W, (i // 3) * H))
    palette = atlas.quantize(colors=192)
    frames = []
    durations = []
    for i in range(FPS * 24):
        t = i / FPS
        im = scene(t)
        # Cross-dissolve between three distinct spatial compositions.
        for boundary in (8, 16):
            if boundary <= t < boundary + 0.4:
                im = Image.blend(
                    scene(boundary - 0.001), im, ease((t - boundary) / 0.4)
                )
        frames.append(im.quantize(palette=palette, dither=Image.Dither.NONE))
        durations.append(90 if i % 3 == 0 else 80)
    durations[-1] = 1300
    frames[0].save(
        out / "demo.gif",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=1,
    )
    scene(14.5).save(out / "demo-poster.png")
    # Review artifact stays outside the public repository when requested.
    review = os.environ.get("SHOWCASE_REVIEW")
    if review:
        atlas.resize((1680, 640)).save(review)
    with Image.open(out / "demo.gif") as im:
        print(
            f"{out.name}: {im.size}, {im.n_frames} frames, {(out / 'demo.gif').stat().st_size} bytes"
        )
