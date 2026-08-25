#!/usr/bin/env python3
"""Renders the PocketADM app icon as PNG — no external dependencies.

The motif is a shell prompt (a chevron and a cursor bar) on the app's own
"Deep Sea" gradient: PocketADM is a server command deck, and a prompt reads as
that instantly at 60 pixels where a server-rack drawing turns to mush.

    ./tools/make-icon.py
"""
import struct
import zlib
from pathlib import Path

SIZE = 1024
SUBSAMPLES = 4          # vertical oversampling per pixel row

# One set per appearance. iOS 18+ shows a light, dark or tinted icon on the
# home screen; supplying all three beats letting the system derive them.
VARIANTS = {
    # light: the palette's page colour lifted toward its accent
    "": {"top": (18, 34, 56), "bottom": (7, 12, 18), "ink": (77, 163, 255)},
    # dark: the same axis, deeper
    "-dark": {"top": (10, 18, 30), "bottom": (3, 6, 10), "ink": (77, 163, 255)},
    # tinted: iOS reads only luminance and applies its own hue
    "-tinted": {"top": (14, 14, 14), "bottom": (48, 48, 48), "ink": (255, 255, 255)},
}


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def thick_segment(p0, p1, width):
    """A line segment as a quad — the chevron is two of these."""
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0
    length = (dx * dx + dy * dy) ** 0.5
    if length == 0:
        return []
    # unit normal, scaled to half the stroke width
    nx, ny = -dy / length * width / 2, dx / length * width / 2
    return [(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)]


def disc(center, radius, segments=32):
    """Round caps and joins — without them the chevron's elbow has a notch."""
    import math
    cx, cy = center
    return [
        (cx + radius * math.cos(2 * math.pi * i / segments),
         cy + radius * math.sin(2 * math.pi * i / segments))
        for i in range(segments)
    ]


def rounded_rect(x0, y0, x1, y1, radius, segments=8):
    import math
    pts = []
    corners = [
        (x1 - radius, y1 - radius, 0),
        (x0 + radius, y1 - radius, 90),
        (x0 + radius, y0 + radius, 180),
        (x1 - radius, y0 + radius, 270),
    ]
    for cx, cy, start in corners:
        for i in range(segments + 1):
            angle = math.radians(start + 90 * i / segments)
            pts.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return pts


def coverage(polygons, width, height):
    """Per-pixel coverage (0.0–1.0) for the *union* of the polygons.

    Each shape is rasterised on its own and combined with max. Running every
    edge in one pass would let the even-odd rule punch the overlaps back out —
    exactly where the chevron's two arms meet.
    """
    acc = [[0.0] * width for _ in range(height)]
    for poly in polygons:
        if len(poly) < 3:
            continue
        for y in range(height):
            row = acc[y]
            for s in range(SUBSAMPLES):
                sy = y + (s + 0.5) / SUBSAMPLES
                # collect x crossings of this scanline
                xs = []
                n = len(poly)
                for i in range(n):
                    (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
                    if (ay <= sy < by) or (by <= sy < ay):
                        xs.append(ax + (sy - ay) / (by - ay) * (bx - ax))
                xs.sort()
                for i in range(0, len(xs) - 1, 2):
                    left, right = xs[i], xs[i + 1]
                    x_start, x_end = max(0, int(left)), min(width - 1, int(right) + 1)
                    for x in range(x_start, x_end + 1):
                        # horizontal overlap of this pixel with the span
                        overlap = min(x + 1.0, right) - max(float(x), left)
                        if overlap > 0:
                            row[x] = max(row[x], min(1.0, overlap)) if SUBSAMPLES == 1 else row[x]
                            # accumulate sub-row contribution
                            acc[y][x] = min(1.0, acc[y][x] + overlap / SUBSAMPLES)
    return acc


def write_png(path, pixels, width, height):
    raw = b"".join(b"\x00" + bytes(row) for row in pixels)
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB, no alpha
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def build(variant, colors, out_dir):
    # The prompt: a chevron with round caps, plus a cursor bar.
    stroke = 84
    elbow = (560, 512)
    shapes = [
        thick_segment((330, 320), elbow, stroke),
        thick_segment(elbow, (330, 704), stroke),
        disc(elbow, stroke / 2),
        disc((330, 320), stroke / 2),
        disc((330, 704), stroke / 2),
        rounded_rect(610, 660, 810, 704 + 40, 22),
    ]
    cov = coverage(shapes, SIZE, SIZE)

    rows = []
    for y in range(SIZE):
        bg = lerp(colors["top"], colors["bottom"], y / (SIZE - 1))
        row = bytearray()
        cov_row = cov[y]
        for x in range(SIZE):
            a = cov_row[x]
            row += bytes(lerp(bg, colors["ink"], a))
        rows.append(row)

    out = out_dir / f"icon-1024{variant}.png"
    write_png(out, rows, SIZE, SIZE)
    print(f"wrote {out}")


def main():
    out_dir = Path(__file__).resolve().parent.parent / "App/Resources/Assets.xcassets/AppIcon.appiconset"
    out_dir.mkdir(parents=True, exist_ok=True)
    for variant, colors in VARIANTS.items():
        build(variant, colors, out_dir)


if __name__ == "__main__":
    main()
