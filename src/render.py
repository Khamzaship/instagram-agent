from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def collect_news(config: dict[str, Any], posted: Any) -> list[dict[str, Any]]:
    candidates = []
        ROOT / "assets/fonts/DejaVuSans.ttf",
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Any, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        proposed = f"{current} {word}".strip()
        if draw.textbbox((0, 0), proposed, font=font)[2] <= width:
            current = proposed
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_slides(carousel: dict[str, Any], item: dict[str, Any]) -> list[str]:
    out_dir = ROOT / "site/media"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", item["id"].lower()).strip("-")
    paths = []
    for index, text in enumerate(carousel["slides"], start=1):
        image = Image.new("RGB", (1080, 1350), (12, 25, 48))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((64, 64, 1016, 1286), radius=44, fill=(18, 41, 73), outline=(56, 189, 248), width=4)
        draw.text((112, 122), "ТАЖИКИСТАН · АВТО И ДОРОГИ", font=_font(30), fill=(125, 211, 252))
        font_size = 72
        while font_size > 38:
            font = _font(font_size)
            lines = _wrap(draw, text, font, 820)
            height = len(lines) * int(font_size * 1.34)
            if height <= 780:
                break
            font_size -= 3
        y = max(320, (1350 - height) // 2 - 20)
        for line in lines:
            draw.text((112, y), line, font=font, fill=(248, 250, 252))
            y += int(font_size * 1.34)
        draw.text((112, 1190), "Источник указан в подписи", font=_font(28), fill=(148, 163, 184))
        draw.text((900, 1190), f"{index}/{len(carousel['slides'])}", font=_font(28), fill=(125, 211, 252))
        path = out_dir / f"{slug}-{index}.jpg"
        image.save(path, "JPEG", quality=91, optimize=True)
        paths.append(path.name)
    return paths

