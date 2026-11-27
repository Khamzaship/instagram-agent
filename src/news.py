"""Сбор новостей из RSS/Atom: свежесть, отбор по теме, дубли, чистка текста.

Только стандартная библиотека Python (никаких лишних зависимостей).
Совместим с run_bot.py: load_config() и collect_news(config, posted).
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "Mozilla/5.0 (compatible; RoadNewsBot/1.0)"
FETCH_TIMEOUT = 25
MAX_BYTES = 5_000_000

# Под какими ключами в config.json могут лежать ленты
FEED_KEYS = ("feeds", "rss_feeds", "sources", "rss", "news_sources", "feed_urls", "urls")

# Слова-признаки темы «авто и дороги». Это начала слов (regex), поэтому
# «автономная» НЕ совпадает с «авто»: нужны полные корни.
DEFAULT_KEYWORDS = [
    r"дтп", r"\bавто\b", r"автомобил", r"автобус", r"автодорог", r"автотрасс",
    r"автомагистрал", r"автотранспорт", r"автовокзал", r"автоинспек", r"автомашин",
    r"автосалон", r"автопарк", r"автоперевоз", r"авари", r"столкновени",
    r"перевернул", r"наезд", r"сбил[аи]?\b", r"пешеход", r"водител", r"\bводитель",
    r"\bмашин(?:а|ы|у|ой|е|ам|ах|ами)?\b", r"легков", r"грузовик", r"грузов(?:ой|ая|ые)\s+(?:автомобил|транспорт|машин)",
    r"\bдорог", r"дорожн", r"\bтрасс", r"шоссе", r"магистрал", r"перевал", r"тоннел",
    r"туннел", r"\bмост(?:а|у|ом|е|ы|ов)?\b", r"асфальт", r"\bтранспорт", r"\bтакси\b",
    r"маршрутк", r"\bгаи\b", r"гибдд", r"\bпдд\b", r"светофор", r"пробк", r"электромобил",
    r"бензин", r"\bтопливн", r"шиномонтаж", r"тонировк", r"техосмотр", r"ремонт\s+дорог",
]

# Служебные хвосты WordPress-лент: «[…] Запись X впервые появилась Y»
_FOOTER_RE = re.compile(
    r"\s*(?:\[[\u2026.\s]*\]\s*)?(?:Запись|Статья|The post)\s.*?"
    r"(?:впервые появил\w*|appeared first on).*$",
    re.IGNORECASE | re.DOTALL,
)


class NewsError(RuntimeError):
    pass


# ---------------------------------------------------------------- конфиг ----
def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else ROOT / "config.json"
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def _feeds_from_config(config: dict[str, Any]) -> list[dict[str, str]]:
    raw = None
    for key in FEED_KEYS:
        if config.get(key):
            raw = config[key]
            break
    if raw is None:
        raise NewsError(
            "В config.json не найден список лент. Ожидался один из ключей: "
            + ", ".join(FEED_KEYS) + ". Сейчас в конфиге есть ключи: " + ", ".join(config.keys())
        )
    feeds: list[dict[str, str]] = []
    for entry in raw:
        if isinstance(entry, str):
            feeds.append({"url": entry, "name": ""})
        elif isinstance(entry, dict):
            url = entry.get("url") or entry.get("rss") or entry.get("feed") or entry.get("link")
            if url:
                feeds.append({"url": url, "name": entry.get("name") or entry.get("title") or ""})
    if not feeds:
        raise NewsError("Список лент в config.json пуст или в неизвестном формате")
    return feeds


# ----------------------------------------------------------- чистка текста ----
def clean_text(text: str | None, limit: int = 1200) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)          # второй проход: &amp;#8230; и подобное
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = _FOOTER_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s*\[[\u2026.\s]*\]\s*$", "", text).strip()
    # обрубок оборванного предложения в конце («… периоду. В»)
    text = re.sub(r"(?<=[.!?\u2026\u00bb\"])\s+[A-Za-z\u0410-\u044f\u0401\u0451]{1,3}$", "", text).strip()
    if len(text) > limit:
        cut = text[:limit]
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        text = cut[: end + 1] if end > limit // 2 else cut.rsplit(" ", 1)[0] + "…"
    return text


def _norm_title(title: str) -> str:
    return re.sub(r"[^\w\s]", "", title.lower()).strip()


# ------------------------------------------------------------ чтение лент ----
def _fetch(url: str) -> bytes:
    last: Exception | None = None
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                return resp.read(MAX_BYTES)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = exc
    raise NewsError(f"не удалось скачать ленту: {type(last).__name__}")


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _child_text(el: ET.Element, *names: str) -> str:
    for child in el:
        if _local(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def _parse_date(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _atom_link(entry: ET.Element) -> str:
    fallback = ""
    for child in entry:
        if _local(child.tag) == "link":
            href = child.attrib.get("href", "")
            if child.attrib.get("rel", "alternate") == "alternate" and href:
                return href
            fallback = fallback or href
    return fallback


IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")


def _find_image(el: ET.Element, html_text: str = "") -> str:
    """Ищет адрес фото записи: enclosure, media:content/thumbnail, первый <img>."""
    for child in el:
        name, a = _local(child.tag), child.attrib
        url = a.get("url") or a.get("href") or ""
        ext_ok = url.lower().split("?")[0].endswith(IMG_EXT)
        if name == "enclosure" and url and (a.get("type", "").startswith("image") or ext_ok):
            return url
        if name == "link" and a.get("rel") == "enclosure" and a.get("type", "").startswith("image"):
            return url
        if name in ("content", "thumbnail") and a.get("url") and (
            name == "thumbnail" or a.get("medium") == "image"
            or a.get("type", "").startswith("image") or ext_ok
        ):
            return a["url"]
    m = re.search(r"<img[^>]+src=[\"']([^\"']+)[\"']", html_text or "", re.IGNORECASE)
    return html.unescape(m.group(1)) if m else ""


def parse_feed(data: bytes) -> tuple[str, list[dict[str, Any]]]:
    """Возвращает (название_ленты, записи). Понимает RSS 2.0 и Atom."""
    root = ET.fromstring(data)
    items: list[dict[str, Any]] = []
    title = ""
    if _local(root.tag) == "feed":                       # Atom
        title = _child_text(root, "title")
        for entry in root:
            if _local(entry.tag) != "entry":
                continue
            items.append({
                "title": _child_text(entry, "title"),
                "link": _atom_link(entry),
                "summary": _child_text(entry, "summary") or _child_text(entry, "content"),
                "date": _parse_date(_child_text(entry, "published", "updated")),
                "image": _find_image(entry, _child_text(entry, "content") or _child_text(entry, "summary")),
            })
    else:                                                # RSS
        channel = next((c for c in root if _local(c.tag) == "channel"), root)
        title = _child_text(channel, "title")
        for item in channel:
            if _local(item.tag) != "item":
                continue
            items.append({
                "title": _child_text(item, "title"),
                "link": _child_text(item, "link"),
                "summary": _child_text(item, "description") or _child_text(item, "encoded"),
                "date": _parse_date(_child_text(item, "pubdate", "date", "published", "updated")),
                "image": _find_image(item, (_child_text(item, "encoded") or "") + (_child_text(item, "description") or "")),
            })
    return title, items


# --------------------------------------------------------------- тема ----
def _compile_keywords(config: dict[str, Any]) -> list[re.Pattern[str]]:
    words = config.get("topic_keywords") or DEFAULT_KEYWORDS
    return [re.compile(w, re.IGNORECASE) for w in words]


def topic_score(title: str, summary: str, patterns: list[re.Pattern[str]]) -> int:
    """Заголовок весит больше описания. 0 = не по теме."""
    t = sum(1 for p in patterns if p.search(title))
    s = sum(1 for p in patterns if p.search(summary))
    if t:
        return 3 * t + s
    return s if s >= 2 else 0     # в одном описании нужно минимум 2 разных признака


# ------------------------------------------------------------ главная ----
def _make_id(link: str) -> str:
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]


def _source_name(feed_name: str, feed_title: str, url: str) -> str:
    name = feed_name or feed_title or urlparse(url).netloc
    return re.sub(r"\s+", " ", html.unescape(name)).strip()


def collect_news(config: dict[str, Any], posted: Any) -> list[dict[str, Any]]:
    if isinstance(posted, dict):
        posted = posted.get("items", [])
    posted = posted or []
    known_ids = {p.get("id") for p in posted if isinstance(p, dict)}
    known_links = {p.get("link") for p in posted if isinstance(p, dict)}
    known_titles = [_norm_title(p.get("title", "")) for p in posted[-150:] if isinstance(p, dict)]

    max_age = float(config.get("max_age_hours", 36))
    limit = int(config.get("max_items", config.get("items_per_run", 1)))
    per_source = int(config.get("max_per_source", 2))
    similarity = float(config.get("title_similarity", 0.8))
    patterns = _compile_keywords(config)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age)

    candidates: list[dict[str, Any]] = []
    for feed in _feeds_from_config(config):
        url = feed["url"]
        try:
            feed_title, entries = parse_feed(_fetch(url))
        except (NewsError, ET.ParseError) as exc:
            logging.warning("Лента пропущена (%s): %s", urlparse(url).netloc, exc)
            continue
        source = _source_name(feed["name"], feed_title, url)
        fresh = on_topic = 0
        for e in entries:
            title = clean_text(e["title"], 300)
            link = (e["link"] or "").strip()
            when = e["date"]
            if not title or not link or not when or when < cutoff:
                continue
            fresh += 1
            summary = clean_text(e["summary"])
            score = topic_score(title, summary, patterns)
            if score <= 0:
                continue
            on_topic += 1
            candidates.append({
                "id": _make_id(link), "title": title, "summary": summary, "link": link,
                "source": source, "published": when.isoformat(), "image": (e.get("image") or "").strip(),
                "_score": score, "_when": when,
            })
        logging.info("Лента %s: записей %d, свежих %d, по теме %d", source, len(entries), fresh, on_topic)

    # свежие и более «по теме» выше
    candidates.sort(key=lambda n: (n["_when"].date(), n["_score"], n["_when"]), reverse=True)

    result: list[dict[str, Any]] = []
    per_src: dict[str, int] = {}
    seen_titles = list(known_titles)
    for n in candidates:
        norm = _norm_title(n["title"])
        if n["id"] in known_ids or n["link"] in known_links:
            continue
        if any(SequenceMatcher(None, norm, t).ratio() >= similarity for t in seen_titles if t):
            continue
        if per_src.get(n["source"], 0) >= per_source:
            continue
        result.append({k: v for k, v in n.items() if not k.startswith("_")})
        per_src[n["source"]] = per_src.get(n["source"], 0) + 1
        seen_titles.append(norm)
        if len(result) >= limit:
            break

    logging.info("Отобрано новостей по теме: %d (из кандидатов: %d)", len(result), len(candidates))
    return result
