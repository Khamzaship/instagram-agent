from future import annotations

import re
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(file).resolve().parents[1]

def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
candidates = [
ROOT / "assets/fonts/DejaVuSans.ttf",
Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
Path("C:/Windows/Fonts/arial.ttf"),
]
for path in candidates:
if path.exists():
return ImageFont.truetype(str(path), size=size)
return ImageFont.load_default()

def _wrap(draw, text, font, width):
lines = []
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

def _load_photo(url: str | None):
if not url:
return None
try:
request = Request(
url,
headers={"User-Agent": "Mozilla/5.0"}
)
with urlopen(request, timeout=12) as response:
data = response.read(8_000_000)
image = Image.open(BytesIO(data)).convert("RGB")
return image
except Exception:
return None

def render_slides(carousel: dict[str, Any], item: dict[str, Any]) -> list[str]:
out_dir = ROOT / "site/media"
out_dir.mkdir(parents=True, exist_ok=True)

slug = re.sub(r"[^a-z0-9]+", "-", item["id"].lower()).strip("-")
photo = _load_photo(item.get("image_url"))
paths = []

slides = carousel.get("slides", [])

for index, text in enumerate(slides, start=1):
    if photo:
        image = ImageOps.fit(photo, (1080, 1350))
        image = image.convert("RGBA")
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle(
            (0, 650, 1080, 1350),
            fill=(0, 0, 0, 175)
        )
        image = Image.alpha_composite(image, overlay).convert("RGB")
    else:
        image = Image.new("RGB", (1080, 1350), (12, 25, 48))

    draw = ImageDraw.Draw(image)

    draw.text(
        (70, 80),
        "ТАЖИКИСТАН · АВТО И ДОРОГИ",
        font=_font(30),
        fill=(125, 211, 252),
    )

    font_size = 66
    while font_size > 34:
        font = _font(font_size)
        lines = _wrap(draw, text, font, 900)
        height = len(lines) * int(font_size * 1.3)
        if height <= 570:
            break
        font_size -= 3

    y = max(700, 1250 - height - 100)

    for line in lines:
        draw.text(
            (75, y),
            line,
            font=font,
            fill=(255, 255, 255),
            stroke_width=2,
            stroke_fill=(0, 0, 0),
        )
        y += int(font_size * 1.3)

    draw.text(
        (75, 1280),
        f"{index}/{len(slides)}",
        font=_font(28),
        fill=(125, 211, 252),
    )

    path = out_dir / f"{slug}-{index}.jpg"
    image.save(path, "JPEG", quality=92, optimize=True)
    paths.append(path.name)

return paths
