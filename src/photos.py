"""Поиск фото для новости: сначала фото из самой новости, затем (по желанию) сток Pexels."""
from __future__ import annotations

import html
import io
import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote, urljoin, urlparse

from PIL import Image

USER_AGENT = "Mozilla/5.0 (compatible; RoadNewsBot/1.0)"
TIMEOUT = 25
MAX_IMAGE_BYTES = 10_000_000
MAX_PAGE_BYTES = 800_000
MIN_WIDTH, MIN_HEIGHT = 700, 400


def _http_get(url: str, referer: str = "", limit: int = MAX_IMAGE_BYTES, headers: dict | None = None) -> bytes:
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("неподдерживаемая ссылка")
    h = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if referer:
        h["Referer"] = referer
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read(limit)


def _download_image(url: str, referer: str = "") -> Image.Image | None:
    """Скачивает и проверяет картинку. None, если не подошла."""
    try:
        data = _http_get(url, referer)
        img = Image.open(io.BytesIO(data))
        img.load()
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        logging.info("Фото не скачалось (%s): %s", type(exc).__name__, url[:120])
        return None
    if img.width < MIN_WIDTH or img.height < MIN_HEIGHT:
        logging.info("Фото слишком маленькое (%dx%d): %s", img.width, img.height, url[:120])
        return None
    return img.convert("RGB")


def _og_image(page_url: str) -> str:
    """Главное изображение страницы (og:image / twitter:image)."""
    try:
        page = _http_get(page_url, limit=MAX_PAGE_BYTES).decode("utf-8", errors="ignore")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        logging.info("Страница новости не открылась (%s)", type(exc).__name__)
        return ""
    for tag in re.findall(r"<meta\b[^>]*>", page, flags=re.IGNORECASE):
        if re.search(r'(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image)["\']', tag, re.IGNORECASE):
            m = re.search(r'content=["\']([^"\']+)["\']', tag, re.IGNORECASE)
            if m:
                return urljoin(page_url, html.unescape(m.group(1)).strip())
    return ""


def _stock_photo(item: dict[str, Any], config: dict[str, Any]) -> tuple[Image.Image | None, str]:
    key = os.getenv("PEXELS_API_KEY", "").strip()
    if not key:
        return None, ""
    query = config.get("stock_query") or "road traffic cars"
    url = f"https://api.pexels.com/v1/search?query={quote(query)}&orientation=portrait&per_page=30"
    try:
        data = json.loads(_http_get(url, limit=2_000_000, headers={"Authorization": key}).decode("utf-8"))
        photos = data.get("photos") or []
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        logging.warning("Pexels недоступен (%s)", type(exc).__name__)
        return None, ""
    if not photos:
        return None, ""
    pick = photos[int(item.get("id", "0") or "0", 16) % len(photos)]
    src = pick.get("src", {})
    img = _download_image(src.get("large2x") or src.get("large") or "")
    if not img:
        return None, ""
    who = pick.get("photographer") or "Pexels"
    return img, f"Фото: {who} / Pexels"


def get_photo(item: dict[str, Any], config: dict[str, Any]) -> tuple[Image.Image | None, str]:
    """Возвращает (изображение, подпись-автор). (None, "") если фото нет."""
    mode = str(config.get("photo_source", "article")).lower()
    if mode == "none":
        return None, ""

    if mode == "article":
        link = item.get("link", "")
        urls: list[str] = []
        if item.get("image"):
            urls.append(item["image"])
        for url in urls:
            img = _download_image(urljoin(link, url), link)
            if img:
                logging.info("Фото взято из ленты новости")
                return img, f"Фото: {item.get('source', '')}".strip()
        og = _og_image(link) if link else ""
        if og:
            img = _download_image(og, link)
            if img:
                logging.info("Фото взято со страницы новости (og:image)")
                return img, f"Фото: {item.get('source', '')}".strip()
        if not config.get("stock_fallback", True):
            return None, ""

    img, credit = _stock_photo(item, config)
    if img:
        logging.info("Фото взято из Pexels")
        return img, credit
    logging.info("Фото не найдено, будет фон без фотографии")
    return None, ""
