"""Рисует слайды карусели 1080x1350. Если есть фото новости, оно становится фоном."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from src.photos import get_photo

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "site" / "media"
W, H = 1080, 1350
MARGIN = 72

FONT_BOLD = [
    ROOT / "assets/fonts/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
FONT_REG = [
    ROOT / "assets/fonts/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    for path in (FONT_BOLD if bold else FONT_REG):
        if os.path.exists(path):
            return ImageFont.truetype(str(path), size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _wrap(text: str, font: ImageFont.ImageFont, max_w: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        cur = ""
        for word in paragraph.split():
            while font.getlength(word) > max_w:      # очень длинное слово
                cut = len(word) - 1
                while cut > 1 and font.getlength(word[:cut]) > max_w:
                    cut -= 1
                if cur:
                    lines.append(cur)
                    cur = ""
                lines.append(word[:cut])
                word = word[cut:]
            test = f"{cur} {word}".strip()
            if font.getlength(test) <= max_w:
                cur = test
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return [l for l in lines if l != ""] or [""]


def _fit(text: str, max_w: int, max_h: int, start: int, minimum: int, bold: bool):
    size = start
    while True:
        font = _font(size, bold)
        lines = _wrap(text, font, max_w)
        line_h = int(size * 1.28)
        if len(lines) * line_h <= max_h or size <= minimum:
            return font, lines, line_h
        size -= 2


def _cover(img: Image.Image) -> Image.Image:
    scale = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1), Image.LANCZOS)
    left, top = (img.width - W) // 2, (img.height - H) // 3   # чуть выше центра: на фото обычно важное сверху
    return img.crop((left, max(top, 0), left + W, max(top, 0) + H))


def _gradient_bg() -> Image.Image:
    top, bottom = (16, 26, 48), (34, 66, 120)
    img = Image.new("RGB", (W, H))
    px = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        px.line([(0, y), (W, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return img


def _shade(img: Image.Image, strength: float = 0.55) -> Image.Image:
    return Image.blend(img, Image.new("RGB", img.size, (0, 0, 0)), strength)


def _bottom_fade(img: Image.Image, height: int = 900, max_alpha: int = 235) -> Image.Image:
    mask = Image.linear_gradient("L").resize((W, height))          # чёрный -> белый сверху вниз
    mask = mask.point(lambda v: int(v / 255 * max_alpha))
    layer = Image.new("RGB", (W, height), (0, 0, 0))
    out = img.copy()
    out.paste(layer, (0, H - height), mask)
    return out


def _text(draw: ImageDraw.ImageDraw, xy, text, font, fill=(255, 255, 255), stroke=2):
    draw.text(xy, text, font=font, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0))


def _footer(draw: ImageDraw.ImageDraw, credit: str, handle: str, idx: int, total: int):
    small = _font(28)
    if credit:
        _text(draw, (MARGIN, H - 70), credit, small, fill=(225, 225, 225), stroke=1)
    if handle:
        w = small.getlength(handle)
        _text(draw, (W - MARGIN - w, H - 70), handle, small, fill=(225, 225, 225), stroke=1)
    if total > 1:
        label = f"{idx}/{total}"
        w = small.getlength(label)
        _text(draw, (W - MARGIN - w, 56), label, small, fill=(225, 225, 225), stroke=1)


def _cover_slide(photo: Image.Image | None, text: str) -> Image.Image:
    base = _cover(photo) if photo else _gradient_bg()
    img = _bottom_fade(base, 960 if photo else 700)
    draw = ImageDraw.Draw(img)
    font, lines, line_h = _fit(text, W - 2 * MARGIN, 640, 80, 44, bold=True)
    block_h = len(lines) * line_h
    y = H - 150 - block_h
    draw.rectangle([MARGIN, y - 34, MARGIN + 120, y - 26], fill=(255, 196, 0))   # акцентная полоса
    for line in lines:
        _text(draw, (MARGIN, y), line, font, stroke=3)
        y += line_h
    return img


def _body_slide(photo: Image.Image | None, text: str) -> Image.Image:
    if photo:
        base = _shade(_cover(photo).filter(ImageFilter.GaussianBlur(26)), 0.5)
    else:
        base = _gradient_bg()
    draw = ImageDraw.Draw(base)
    font, lines, line_h = _fit(text, W - 2 * MARGIN, 840, 62, 36, bold=False)
    block_h = len(lines) * line_h
    y = (H - block_h) // 2
    draw.rectangle([MARGIN, y - 44, MARGIN + 120, y - 36], fill=(255, 196, 0))
    for line in lines:
        _text(draw, (MARGIN, y), line, font, stroke=2)
        y += line_h
    return base


def render_slides(carousel: dict[str, Any], item: dict[str, Any]) -> list[str]:
    try:
        from src.news import load_config
        config = load_config()
    except Exception:                                       # noqa: BLE001
        config = {}
    photo, credit = get_photo(item, config)
    handle = str(config.get("account_handle", "")).strip()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    slides = carousel["slides"]
    names: list[str] = []
    for i, text in enumerate(slides, 1):
        img = _cover_slide(photo, text) if i == 1 else _body_slide(photo, text)
        _footer(ImageDraw.Draw(img), credit, handle, i, len(slides))
        name = f"{item['id']}-{i}.jpg"
        img.save(OUT_DIR / name, "JPEG", quality=90, optimize=True)
        names.append(name)
    logging.info("Картинки готовы: %d шт. (фото: %s)", len(names), "да" if photo else "нет")
    return names
