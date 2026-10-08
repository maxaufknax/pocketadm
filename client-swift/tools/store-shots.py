#!/usr/bin/env python3
"""Turn raw simulator screenshots into App Store images in PocketADM's style.

The release workflow photographs the real app on an iPhone simulator (demo
server, one shot per tab) and this lays each shot into a phone on a black
canvas under a bold headline whose key words carry the brand gradient — the
look of the hand-made 1.x store images. One set per language, at 1284 x 2778
(the App Store's 6.5" iPhone slot, the one PocketADM's listing uses).

    python3 tools/store-shots.py RAW_DIR OUT_DIR [--locales en-US,de-DE]

RAW_DIR holds raw-<name>.png (dashboard, assistant, watch, files, containers,
terminal); OUT_DIR/<locale>/01-dashboard.png … are written. Pillow is the only
dependency; the fonts (Inter, SIL OFL) ship in AppStore/fonts.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1284, 2778
FONTS = Path(__file__).resolve().parent.parent / "AppStore" / "fonts"
GRAD = ((0x4F, 0xE3, 0xE0), (0x1D, 0x62, 0xD8))   # cyan -> blue, as in the 1.x images
SUB = (0x4D, 0xA3, 0xFF)

# {braces} mark the words that get the gradient
CAPTIONS = {
    "en-US": [
        ("dashboard", ["Your server,", "{in your pocket.}"], ""),
        ("assistant", ["An {AI agent}", "that works on", "your server"],
         "+ your own key or Claude / Codex plan"),
        ("watch", ["A {watch} that", "writes when", "it matters"], "and answers when you ask it"),
        ("files", ["Every {file},", "like in an editor"], ""),
        ("containers", ["Every {container},", "one tap away"], ""),
        ("terminal", ["A {real terminal},", "wherever you are"], ""),
    ],
    "de-DE": [
        ("dashboard", ["Dein Server,", "{in deiner Tasche.}"], ""),
        ("assistant", ["Ein {KI-Agent},", "der auf deinem", "Server arbeitet"],
         "+ eigener Key oder Claude-/Codex-Abo"),
        ("watch", ["Ein {Wächter},", "der schreibt, wenn", "es wichtig ist"], "und antwortet, wenn du fragst"),
        ("files", ["Jede {Datei},", "wie im Editor"], ""),
        ("containers", ["Jeder {Container},", "nur einen Tipp entfernt"], ""),
        ("terminal", ["Ein {echtes Terminal},", "wo immer du bist"], ""),
    ],
}


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def _segments(line: str) -> list[tuple[str, bool]]:
    parts = re.split(r"(\{[^}]*\})", line)
    return [(p[1:-1], True) if p.startswith("{") else (p, False) for p in parts if p]


def _line_width(font, line: str) -> int:
    return int(sum(font.getlength(text) for text, _ in _segments(line)))


def _gradient(width: int, height: int) -> Image.Image:
    grad = Image.new("RGB", (max(width, 1), max(height, 1)))
    px = grad.load()
    for x in range(grad.width):
        t = x / max(grad.width - 1, 1)
        c = tuple(int(GRAD[0][i] + (GRAD[1][i] - GRAD[0][i]) * t) for i in range(3))
        for y in range(grad.height):
            px[x, y] = c
    return grad


def _draw_headline(canvas: Image.Image, lines: list[str], top: int) -> int:
    """Centered, auto-fitted headline. Returns the y below it."""
    size = 150
    while size > 70:
        font = _font("InterDisplay-Black.ttf", size)
        if max(_line_width(font, l) for l in lines) <= W - 140:
            break
        size -= 4
    draw = ImageDraw.Draw(canvas)
    ascent, descent = font.getmetrics()
    step = int((ascent + descent) * 0.9)
    y = top
    for line in lines:
        x = (W - _line_width(font, line)) // 2
        for text, accent in _segments(line):
            width = int(font.getlength(text))
            if accent:
                mask = Image.new("L", (width + 8, ascent + descent + 8), 0)
                ImageDraw.Draw(mask).text((0, 0), text, font=font, fill=255)
                canvas.paste(_gradient(mask.width, mask.height), (x, y), mask)
            else:
                draw.text((x, y), text, font=font, fill=(255, 255, 255))
            x += width
        y += step
    return y


def _phone(shot: Image.Image, width: int) -> Image.Image:
    """The screenshot inside a dark phone frame with a dynamic island."""
    bezel = int(width * 0.035)
    screen_w = width - 2 * bezel
    screen_h = int(shot.height * screen_w / shot.width)
    height = screen_h + 2 * bezel
    radius = int(width * 0.165)
    phone = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(phone)
    d.rounded_rectangle((0, 0, width - 1, height - 1), radius, fill=(58, 58, 62, 255))
    d.rounded_rectangle((5, 5, width - 6, height - 6), radius - 5, fill=(20, 20, 22, 255))
    screen = shot.convert("RGB").resize((screen_w, screen_h), Image.LANCZOS)
    mask = Image.new("L", (screen_w, screen_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, screen_w - 1, screen_h - 1),
                                           radius - bezel, fill=255)
    phone.paste(screen, (bezel, bezel), mask)
    island_w, island_h = int(screen_w * 0.29), int(screen_w * 0.085)
    ix, iy = (width - island_w) // 2, bezel + int(screen_w * 0.03)
    d.rounded_rectangle((ix, iy, ix + island_w, iy + island_h), island_h // 2, fill=(0, 0, 0, 255))
    return phone


def compose(raw: Path, lines: list[str], sub: str) -> Image.Image:
    canvas = Image.new("RGB", (W, H), (0, 0, 0))
    # a faint blue glow behind the headline, so black is not flat
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse((-W * 0.2, -H * 0.12, W * 1.2, H * 0.32), fill=60)
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    canvas.paste(Image.new("RGB", (W, H), (14, 40, 80)), (0, 0), glow)

    y = _draw_headline(canvas, lines, top=170)
    if sub:
        font = _font("Inter-SemiBold.ttf", 46)
        ImageDraw.Draw(canvas).text((W - 90 - int(font.getlength(sub)), y + 12), sub,
                                    font=font, fill=SUB)
        y += 90
    shot = Image.open(raw)
    phone_w = 960
    phone = _phone(shot, phone_w)
    top = max(y + 70, H - phone.height - 110)
    if top + phone.height > H - 60:            # tall headline: shrink the phone to fit
        scale = (H - 60 - (y + 70)) / phone.height
        phone = _phone(shot, int(phone_w * scale))
        top = y + 70
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).rounded_rectangle(
        ((W - phone.width) // 2 - 10, top + 30, (W + phone.width) // 2 + 10, top + phone.height + 40),
        180, fill=140)
    canvas.paste((0, 0, 0), (0, 0), shadow.filter(ImageFilter.GaussianBlur(40)))
    canvas.paste(phone, ((W - phone.width) // 2, top), phone)
    return canvas


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("raw_dir")
    p.add_argument("out_dir")
    p.add_argument("--locales", default="en-US,de-DE")
    args = p.parse_args()
    raw_dir, out_dir = Path(args.raw_dir), Path(args.out_dir)
    for locale in args.locales.split(","):
        target = out_dir / locale
        target.mkdir(parents=True, exist_ok=True)
        for n, (tab, lines, sub) in enumerate(CAPTIONS[locale], start=1):
            raw = raw_dir / f"raw-{tab}.png"
            if not raw.exists():
                raise SystemExit(f"missing {raw}")
            image = compose(raw, lines, sub)
            assert image.size == (W, H)
            image.save(target / f"{n:02d}-{tab}.png", optimize=True)
            print(f"✓ {locale}/{n:02d}-{tab}.png")


if __name__ == "__main__":
    main()
