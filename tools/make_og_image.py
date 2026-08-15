"""Generate web/static/og.png -- the link preview card.

A link to this tool gets posted into forums, subreddits and printing groups,
and a URL with no og:image renders there as a bare line of text. That is the
difference between a post people click and a post they scroll past, which is
the entire reason this file exists.

Run manually when the card's wording or palette changes:

    .venv/Scripts/python tools/make_og_image.py

Pillow is a *build-time* dependency only, deliberately kept out of
requirements.txt -- the generated PNG is committed and the server only ever
serves it as a static file. Adding an imaging library to a web dyno to
produce one unchanging image would be paying rent forever for a one-off.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "web" / "static" / "og.png"

# Facebook, Reddit, Discord and X all crop toward 1.91:1; 1200x630 is the size
# every one of them accepts without re-encoding.
W, H = 1200, 630

# The site's own light palette, so the card and the page it links to look like
# the same product. Deliberately not theme-aware: a preview card is rendered by
# the platform, not the reader's browser, and has no dark mode to respond to.
BG = "#faf9f7"
BG_ACCENT = "#f2efea"
TEXT = "#191817"
MUTED = "#6d6862"
BORDER = "#e2ddd6"
ACCENT = "#b4502c"
U1 = "#2f7d6d"
BAMBU = "#1f6f4a"

FONTS = Path("C:/Windows/Fonts")


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS / name
    if not path.exists():
        raise SystemExit(f"font not found: {path} -- adjust FONTS for this machine")
    return ImageFont.truetype(str(path), size)


def _rounded(draw: ImageDraw.ImageDraw, box, radius, **kw) -> None:
    draw.rounded_rectangle(box, radius=radius, **kw)


def build() -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    bold = _font("segoeuib.ttf", 92)
    regular = _font("segoeui.ttf", 38)
    small = _font("segoeuib.ttf", 30)
    tiny = _font("segoeui.ttf", 27)

    left = 96
    y = 150

    # Wordmark, preceded by the same two-arrow glyph the site uses. Drawn with
    # primitives rather than an icon font so this file has no asset to lose.
    ax, ay = left, y + 46
    d.line([(ax, ay - 14), (ax + 62, ay - 14)], fill=ACCENT, width=8)
    d.line([(ax + 44, ay - 32), (ax + 62, ay - 14)], fill=ACCENT, width=8)
    d.line([(ax, ay + 14), (ax + 62, ay + 14)], fill=ACCENT, width=8)
    d.line([(ax, ay + 14), (ax + 18, ay + 32)], fill=ACCENT, width=8)

    d.text((left + 92, y), "Crossprint", font=bold, fill=TEXT)
    y += 128

    d.text((left, y), "Move a sliced 3MF project", font=regular, fill=MUTED)
    d.text((left, y + 52), "onto a different printer.", font=regular, fill=MUTED)
    y += 148

    # The route badge -- the product's whole point, and the one thing a reader
    # scanning a forum thread needs to take away.
    pad_x, pad_y, gap = 30, 20, 26
    w_u1 = d.textlength("Snapmaker U1", font=small)
    w_bambu = d.textlength("Bambu Lab", font=small)
    arrow_w = 54
    badge_w = pad_x * 2 + w_u1 + gap + arrow_w + gap + w_bambu
    badge_h = pad_y * 2 + 36
    _rounded(d, (left, y, left + badge_w, y + badge_h), badge_h // 2, fill=BG_ACCENT, outline=BORDER, width=2)

    tx = left + pad_x
    d.text((tx, y + pad_y), "Snapmaker U1", font=small, fill=U1)
    tx += w_u1 + gap
    mid = y + badge_h / 2
    d.line([(tx, mid - 9), (tx + arrow_w, mid - 9)], fill=MUTED, width=5)
    d.line([(tx + arrow_w - 16, mid - 22), (tx + arrow_w, mid - 9)], fill=MUTED, width=5)
    d.line([(tx, mid + 9), (tx + arrow_w, mid + 9)], fill=MUTED, width=5)
    d.line([(tx, mid + 9), (tx + 16, mid + 22)], fill=MUTED, width=5)
    tx += arrow_w + gap
    d.text((tx, y + pad_y), "Bambu Lab", font=small, fill=BAMBU)

    d.text((left, H - 88), "Free and open source  ·  crossprint.onrender.com", font=tiny, fill=MUTED)

    _draw_beds(d)

    # A restrained accent edge, so the card reads as designed rather than blank.
    d.rectangle((0, 0, 14, H), fill=ACCENT)
    return img


def _draw_beds(d: ImageDraw.ImageDraw) -> None:
    """The swap glyph, oversized and low-contrast, filling the right half.

    A literal diagram was tried here first -- two build plates of different
    sizes with the same object on each, which is what conversion actually
    recomputes. At this scale the two objects overlapped into a single blob
    and the nested plates read as noise. A motif that cannot be misread beats
    a diagram that can: the glyph is the site's own, so the card and the page
    it links to are recognisably the same product.
    """
    cx, cy = 900, 300
    span, gap, head, weight = 150, 46, 44, 13

    for direction in (1, -1):
        y = cy - direction * gap
        x_end = cx + direction * span
        d.line([(cx - direction * span, y), (x_end, y)], fill=BORDER, width=weight)
        # Arrowhead, drawn as two strokes meeting at the tip.
        d.line([(x_end - direction * head, y - head), (x_end, y)], fill=BORDER, width=weight)
        d.line([(x_end - direction * head, y + head), (x_end, y)], fill=BORDER, width=weight)


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    build().save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)", file=sys.stderr)
