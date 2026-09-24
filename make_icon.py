#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate PDFTextEditor application icons with Pillow only (no cairosvg).

The vector source of truth is ``icon.svg``; this script redraws exactly the
same geometry with Pillow's ``ImageDraw`` at 4x supersampling and downsamples
with LANCZOS, which gives smooth anti-aliased "vector-like" edges.

Outputs (written next to this script):
    icon.png          512x512 RGBA, transparent background
    icon.ico          multi-size ICO (16,24,32,48,64,128,256)
    icon_preview.png  self-check sheet: native + magnified sizes on a light
                      and a dark background

Design summary
--------------
A rounded sheet of paper has its top-right corner folded (white flap). A pen
runs diagonally across the lower-right of the page; it borrows the page's deep
green so both shapes fuse into one silhouette, while a white outline (clipped
to the page), a barrel highlight and the nib keep the pen readable where it
overlaps the document.

Colors: #0E6B4F (primary green) + #FFFFFF (accent), transparent background.
"""

from PIL import Image, ImageDraw, ImageFont, ImageChops

# --------------------------------------------------------------------------- #
# canvas / palette
# --------------------------------------------------------------------------- #
SIZE = 512          # final icon size
SS = 4              # supersampling factor (draw big, shrink for anti-aliasing)
W = SIZE * SS

GREEN = (14, 107, 79, 255)     # #0E6B4F
WHITE = (255, 255, 255, 255)
CLEAR = (0, 0, 0, 0)           # transparent

# --------------------------------------------------------------------------- #
# geometry (all numbers are in the final 512x512 coordinate space)
# --------------------------------------------------------------------------- #
# document: rounded rectangle, top-right corner removed by a 45 deg cut
DOC = (120, 66, 396, 446)          # x0, y0, x1, y1
DOC_R = 34                         # corner radius
CUT = [(296, 66), (410, 66), (410, 180)]      # erased corner triangle
FLAP = [(296, 66), (396, 166), (296, 166)]    # white folded flap

# pen: S=(214,150) start -> B=(398,334) nib base -> T=(478,414) tip
PEN_POLY = [(238, 126), (422, 310), (478, 414), (374, 358), (190, 174)]
PEN_ROUND = (214, 150, 34)          # rounded tail: cx, cy, r
HL_LINE = (230, 150, 398, 318)      # white barrel highlight
NIB_LINE = (422, 310, 374, 358)     # white nib-base separation


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def new_layer():
    return Image.new("RGBA", (W, W), CLEAR)


def spts(seq):
    """scale a list of (x, y) points to supersampled space"""
    return [(x * SS, y * SS) for x, y in seq]


def sbox(box):
    return [box[0] * SS, box[1] * SS, box[2] * SS, box[3] * SS]


def circle_box(cx, cy, r):
    return [(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS]


# --------------------------------------------------------------------------- #
# layers
# --------------------------------------------------------------------------- #
def render_doc():
    """Green sheet with the top-right corner cut away and a white flap."""
    layer = new_layer()
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(sbox(DOC), radius=DOC_R * SS, fill=GREEN)
    d.polygon(spts(CUT), fill=CLEAR)     # erase the corner (transparent notch)
    d.polygon(spts(FLAP), fill=WHITE)    # folded-over flap
    return layer


def render_pen_outline():
    """White outline of the pen (later clipped to the page)."""
    layer = new_layer()
    d = ImageDraw.Draw(layer)
    pts = spts(PEN_POLY)
    d.line(pts + [pts[0]], fill=WHITE, width=12 * SS, joint="curve")
    cx, cy, r = PEN_ROUND
    d.ellipse(circle_box(cx, cy, r + 6), outline=WHITE, width=12 * SS)
    return layer


def render_pen_fill():
    """Solid green pen body + rounded tail (same green as the page)."""
    layer = new_layer()
    d = ImageDraw.Draw(layer)
    d.polygon(spts(PEN_POLY), fill=GREEN)
    cx, cy, r = PEN_ROUND
    d.ellipse(circle_box(cx, cy, r), fill=GREEN)
    return layer


def render_details():
    """White accents: nib-base divider and the barrel highlight."""
    layer = new_layer()
    d = ImageDraw.Draw(layer)
    x1, y1, x2, y2 = NIB_LINE
    d.line([x1 * SS, y1 * SS, x2 * SS, y2 * SS], fill=WHITE, width=12 * SS)
    x1, y1, x2, y2 = HL_LINE
    d.line([x1 * SS, y1 * SS, x2 * SS, y2 * SS], fill=WHITE, width=9 * SS)
    return layer


def compose_master():
    """Composite every layer and downsample to the final 512x512."""
    img = new_layer()

    doc = render_doc()
    img.alpha_composite(doc)

    # pen outline only where it lies over the page -> clean overlap cue,
    # no white halo around the nib on the transparent background
    outline = render_pen_outline()
    outline.putalpha(ImageChops.multiply(outline.getchannel("A"),
                                         doc.getchannel("A")))
    img.alpha_composite(outline)

    img.alpha_composite(render_pen_fill())
    img.alpha_composite(render_details())

    return img.resize((SIZE, SIZE), Image.LANCZOS)


# --------------------------------------------------------------------------- #
# file outputs
# --------------------------------------------------------------------------- #
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def build_icons():
    master = compose_master()
    master.save("icon.png")
    master.save("icon.ico", format="ICO",
                sizes=[(s, s) for s in ICO_SIZES])
    return master


# --------------------------------------------------------------------------- #
# preview sheet
# --------------------------------------------------------------------------- #
LIGHT_BG = (241, 243, 246, 255)
DARK_BG = (24, 26, 30, 255)
NATIVE_SIZES = [16, 24, 32, 48, 64, 128, 256]
MAGNIFIED = [(16, 8), (32, 4), (48, 3), (64, 2), (128, 1)]  # (size, factor)


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                       # very old Pillow fallback
        return ImageFont.load_default()


def _label(draw, xy, text, color, font):
    x, y = xy
    draw.text((x, y), text, fill=color, font=font)


def _text_w(font, text):
    """Text width in pixels, tolerant of old Pillow versions."""
    try:
        return font.getlength(text)
    except Exception:
        return len(text) * 12


def _panel(bg, fg):
    """Build one preview panel (native row + magnified row) on a solid bg."""
    pad = 28
    gap = 22
    title_h = 36
    cell_h = 276            # room for the 256px icon
    label_h = 30
    mag_h = max(s * f for s, f in MAGNIFIED)

    font = _font(22)
    small = _font(20)

    # --- columns: wide enough for both the icon and its caption ---
    nat_cols = [max(s, int(_text_w(small, f"{s}px")) + 12) for s in NATIVE_SIZES]
    nat_w = sum(nat_cols) + gap * (len(NATIVE_SIZES) - 1)
    mag_imgs = [(s, f, s * f) for s, f in MAGNIFIED]
    mag_w = sum(px for _, _, px in mag_imgs) + gap * (len(mag_imgs) - 1)

    inner_w = max(nat_w, mag_w)
    width = inner_w + pad * 2
    height = pad + title_h + cell_h + label_h + 12 + title_h + mag_h + label_h + pad

    panel = Image.new("RGBA", (width, height), bg)
    d = ImageDraw.Draw(panel)

    _top = pad
    baseline = _top + title_h + cell_h
    _label(d, (_top, pad), "ACTUAL SIZE  (1px = 1px), transparent background",
           fg, font)

    # native sizes, bottom-aligned to a common baseline, centred columns
    x = pad
    for s, cw in zip(NATIVE_SIZES, nat_cols):
        icon = _load_size(s)
        panel.alpha_composite(icon, (x + (cw - s) // 2, baseline - s))
        cap = f"{s}px"
        _label(d, (x + (cw - _text_w(small, cap)) / 2, baseline + 6), cap, fg, small)
        x += cw + gap

    # magnified row
    mag_title_y = baseline + label_h + 12
    _label(d, (_top, mag_title_y),
           "MAGNIFIED 16 / 32 / 48 px  (nearest-neighbour)", fg, font)
    mag_top = mag_title_y + title_h
    x = pad
    for s, f, px in mag_imgs:
        icon = _load_size(s).resize((px, px), Image.NEAREST)
        panel.alpha_composite(icon, (x, mag_top))
        _label(d, (x, mag_top + px + 6), f"{s} -> {px}px  x{f}", fg, small)
        x += px + gap

    return panel


_MASTER = None


def _load_size(size):
    """Downscale the master to a given size (kept in memory for the preview)."""
    if size == SIZE:
        return _MASTER
    return _MASTER.resize((size, size), Image.LANCZOS)


def build_preview():
    light = _panel(LIGHT_BG, (30, 34, 40, 255))
    dark = _panel(DARK_BG, (232, 236, 242, 255))

    gap = 24
    width = max(light.width, dark.width)
    height = light.height + gap + dark.height
    sheet = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    sheet.alpha_composite(light, ((width - light.width) // 2, 0))
    sheet.alpha_composite(dark, ((width - dark.width) // 2, light.height + gap))
    sheet.convert("RGB").save("icon_preview.png")


# --------------------------------------------------------------------------- #
# self-check
# --------------------------------------------------------------------------- #
def verify_ico():
    ico = Image.open("icon.ico")
    got = sorted(ico.ico.sizes())
    want = set((s, s) for s in ICO_SIZES)
    missing = want - set(got)
    print("icon.ico sizes :", got)
    print("required sizes :", sorted(want))
    print("missing        :", sorted(missing) if missing else "none  [OK]")
    assert not missing, f"ICO is missing sizes: {sorted(missing)}"


def main():
    global _MASTER
    _MASTER = build_icons()
    print("icon.png       :", _MASTER.size, _MASTER.mode)
    verify_ico()

    # transparency check
    alpha = _MASTER.getchannel("A")
    print("alpha range    :", alpha.getextrema())

    build_preview()
    print("icon_preview.png: generated")


if __name__ == "__main__":
    main()
