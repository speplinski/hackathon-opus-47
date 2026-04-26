#!/usr/bin/env python3
"""
Generate the YouTube thumbnail directly as PNG, no browser screenshot.
Uses Pillow + downloaded Brygada 1918 italic font.

Output: thumbnail.png at 1920x1080 (16:9), <2MB JPG-equivalent quality.
"""

import io
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ── Brand palette (matches scene_07d_opus.html) ──
BG_CORAL    = (201, 89, 60)        # #C9593C
FG_CREAM    = (250, 248, 241)      # #FAF8F1
PUNCH_INK   = (42, 10, 14)         # #2A0A0E

# ── Output canvas ──
W, H = 1920, 1080
OUT = Path(__file__).parent / "thumbnail.png"

# ── Font: Brygada 1918 italic (variable — wght axis covers 400..700). ──
# Google Fonts repo direct download (one TTF carries the full italic axis).
FONT_URL = "https://github.com/google/fonts/raw/main/ofl/brygada1918/Brygada1918-Italic%5Bwght%5D.ttf"
FONT_LOCAL = Path("/tmp/Brygada-Italic.ttf")

def fetch_font(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 100_000:
        return dest
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        dest.write_bytes(r.read())
    return dest

def main():
    img = Image.new("RGB", (W, H), BG_CORAL)
    draw = ImageDraw.Draw(img)

    font_path = str(fetch_font(FONT_URL, FONT_LOCAL))
    # Single italic file; wght axis varies. Both spans use the same TTF
    # — the punch fragment gets visual weight from its darker fill colour,
    # not a heavier weight, matching scene_07d_opus.html.
    f_normal = ImageFont.truetype(font_path, size=132)
    f_punch  = ImageFont.truetype(font_path, size=132)

    # Compose the line: "Surfaced by " + "Opus 4.7" (punch ink) + "."
    pre_text   = "Surfaced by "
    punch_text = "Opus 4.7"
    post_text  = "."

    pre_w   = draw.textlength(pre_text,   font=f_normal)
    punch_w = draw.textlength(punch_text, font=f_punch)
    post_w  = draw.textlength(post_text,  font=f_normal)
    total_w = pre_w + punch_w + post_w

    # Centre vertically — use bbox to find true baseline.
    bbox = draw.textbbox((0, 0), pre_text + punch_text + post_text, font=f_normal)
    line_h = bbox[3] - bbox[1]

    x_start = (W - total_w) / 2
    y_start = (H - line_h) / 2 - bbox[1]   # subtract top-padding from font bbox

    x = x_start
    draw.text((x, y_start), pre_text,   font=f_normal, fill=FG_CREAM)
    x += pre_w
    draw.text((x, y_start), punch_text, font=f_punch,  fill=PUNCH_INK)
    x += punch_w
    draw.text((x, y_start), post_text,  font=f_normal, fill=FG_CREAM)

    img.save(OUT, "PNG", optimize=True)
    print(f"✓ written {OUT}  ({OUT.stat().st_size // 1024} KB)")

if __name__ == "__main__":
    main()
