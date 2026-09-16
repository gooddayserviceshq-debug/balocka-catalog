#!/usr/bin/env python3
"""Generate og:image share cards for the four balocka-catalog pages.

Design rule: NO recognizable faces. /season/ is noindex specifically because it
holds galleries of minors; a share unfurl must not leak a player's face into a
group chat preview. So the cards are brand typography over a heavily darkened,
abstracted action texture, plus the sheep+lock+A mark.

Output: 1200x630 JPEG (the OG spec size, 1.91:1).
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

W, H = 1200, 630
PURPLE_DEEP = (45, 0, 79)
PURPLE = (75, 0, 130)
GOLD = (255, 184, 28)
RED = (216, 55, 47)
WHITE = (255, 255, 255)
MUTED = (200, 193, 220)

FONT_DIR = "/System/Library/Fonts/Supplemental"
F_BLACK = os.path.join(FONT_DIR, "Arial Black.ttf")
F_BOLD = os.path.join(FONT_DIR, "Arial Bold.ttf")
F_REG = os.path.join(FONT_DIR, "Arial.ttf")


def font(path, size):
    return ImageFont.truetype(path, size)


def text_w(d, s, f):
    return d.textbbox((0, 0), s, font=f)[2]


def fit(d, s, path, start, max_w):
    """Shrink font until the string clears max_w. Never hand-tune coords."""
    size = start
    while size > 12:
        f = font(path, size)
        if text_w(d, s, f) <= max_w:
            return f
        size -= 2
    return font(path, 12)


def gradient_bg():
    base = Image.new("RGB", (W, H), PURPLE_DEEP)
    top = Image.new("RGB", (W, H), PURPLE)
    mask = Image.linear_gradient("L").resize((W, H)).rotate(180)
    return Image.composite(top, base, mask)


def texture(src_path, opacity=0.18, blur=2.0):
    """Darkened, blurred action texture. Blur + low opacity = no identifiable face."""
    if not os.path.exists(src_path):
        return None
    im = Image.open(src_path).convert("RGB")
    # cover-crop to 1200x630
    sr, tr = im.width / im.height, W / H
    if sr > tr:
        nw = int(im.height * tr)
        im = im.crop(((im.width - nw) // 2, 0, (im.width + nw) // 2, im.height))
    else:
        nh = int(im.width / tr)
        im = im.crop((0, (im.height - nh) // 2, im.width, (im.height + nh) // 2))
    im = im.resize((W, H), Image.LANCZOS)
    im = im.filter(ImageFilter.GaussianBlur(blur))
    im = ImageEnhance.Brightness(im).enhance(0.55)
    return im, opacity


def paste_mark(img, mark_path, x, y, h=52):
    if not os.path.exists(mark_path):
        return 0
    m = Image.open(mark_path).convert("RGBA")
    w = int(m.width * (h / m.height))
    m = m.resize((w, h), Image.LANCZOS)
    # mark.png ships on white; knock the white out so it sits on the purple
    px = m.load()
    for j in range(m.height):
        for i in range(m.width):
            r, g, b, a = px[i, j]
            if r > 240 and g > 240 and b > 240:
                px[i, j] = (r, g, b, 0)
    img.paste(m, (x, y), m)
    return w


def card(out, headline, sub, kicker, tex_src=None, accent=GOLD, mark="season/mark.png",
         tex_opacity=0.18, base=None, kicker_color=None):
    img = gradient_bg() if base is None else Image.new("RGB", (W, H), base)
    t = texture(tex_src) if tex_src else None
    if t:
        im, _ = t
        img = Image.blend(img, im, tex_opacity)
    d = ImageDraw.Draw(img)

    PAD = 72
    MAXW = W - PAD * 2

    # top accent rule
    d.rectangle([0, 0, W, 8], fill=accent)

    # brand mark + handle
    mw = paste_mark(img, mark, PAD, 50, 56)
    d = ImageDraw.Draw(img)
    fh = font(F_BOLD, 26)
    d.text((PAD + mw + 16, 54 + 12), "@balocka_creative", font=fh, fill=WHITE)

    # --- measure the whole text block first, then vertically center it ---
    fk = fit(d, kicker, F_BOLD, 26, MAXW)
    fhl = fit(d, headline, F_BLACK, 78, MAXW)
    words, lines, cur = headline.split(), [], ""
    for w_ in words:
        trial = (cur + " " + w_).strip()
        if text_w(d, trial, fhl) <= MAXW:
            cur = trial
        else:
            lines.append(cur)
            cur = w_
    lines.append(cur)
    lines = lines[:3]
    fs = fit(d, sub, F_REG, 30, MAXW)

    GAP_K, GAP_S, LH = 24, 46, 1.12
    block_h = fk.size + GAP_K + int(fhl.size * LH) * len(lines) + GAP_S + fs.size

    TOP_SAFE, BOT_SAFE = 150, 104  # header band above, footer rule below
    y = TOP_SAFE + max(0, ((H - BOT_SAFE) - TOP_SAFE - block_h) // 2)

    d.text((PAD, y), kicker.upper(), font=fk, fill=kicker_color or accent)
    y += fk.size + GAP_K
    for ln in lines:
        d.text((PAD, y), ln, font=fhl, fill=WHITE)
        y += int(fhl.size * LH)
    y += GAP_S - int(fhl.size * (LH - 1))
    d.text((PAD, y), sub, font=fs, fill=MUTED)

    # footer: thin rule + the canonical host, so a forwarded card still says where it goes
    d.line([(PAD, H - 78), (W - PAD, H - 78)], fill=(255, 255, 255, 40), width=1)
    ff = font(F_BOLD, 21)
    d.text((PAD, H - 58), "gooddayserviceshq-debug.github.io/balocka-catalog",
           font=ff, fill=MUTED)

    # bottom bar
    d.rectangle([0, H - 10, W, H], fill=accent)

    img.save(out, "JPEG", quality=88, optimize=True)
    return out


if __name__ == "__main__":
    os.makedirs("og", exist_ok=True)

    card("og/og-season.jpg",
         "Smyrna Bulldogs Football",
         "Free for players and families. Always. No watermark on your copy.",
         "2026 Season Photo Hub",
         tex_src="bg_stadium.jpg")

    card("og/og-home.jpg",
         "Smyrna Bulldogs Photo Catalog",
         "Shot from the stands by a Bulldogs parent - the same view you had.",
         "Balocka Creative",
         tex_src="hero_bg.jpg")

    card("og/og-bestof.jpg",
         "Best of Smyrna Bulldogs Football",
         "The standout frames of the season, in one place.",
         "Balocka Creative",
         tex_src="hero_bg.jpg")

    card("og/og-topgun.jpg",
         "9/11 Top Gun Run",
         "City of Smyrna - event coverage by Balocka Creative.",
         "Civic Event Photography",
         tex_src="topgun/img/topgun_01.jpg",
         accent=(178, 34, 52),
         # navy base + a heavier texture blend so the flags read as flags, not as
         # purple noise; white kicker because red-on-navy fails contrast.
         base=(18, 26, 56),
         tex_opacity=0.52,
         kicker_color=(255, 255, 255))

    for f in sorted(os.listdir("og")):
        p = os.path.join("og", f)
        print(f"{f}  {os.path.getsize(p)/1024:.0f} KB  {Image.open(p).size}")
