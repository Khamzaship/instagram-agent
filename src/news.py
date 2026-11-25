from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import feedparser

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger(__name__)


def load_config() -> dict[str, Any]:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def _clean_html(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", value).strip()


def _parse_date(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(key)
        if value:
            import calendar
            return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    return None


def _normalise(text: str) -> str:
    return re.sub(r"[^\w\s]", " ", text.casefold(), flags=re.UNICODE).strip()


def _relevant(item: dict[str, Any], config: dict[str, Any]) -> bool:
    text = f"{item['title']} {item['summary']}".casefold()
    has_topic = any(term.casefold() in text for term in config["keywords"])
    has_region = any(term.casefold() in text for term in config["country_terms"])
    return has_topic and has_region


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_links: set[str] = set()
    seen_words: list[set[str]] = []
    result = []
    for item in items:
        link = item["link"].split("#", 1)[0].rstrip("/")
        if link in seen_links:
            continue
        words = set(_normalise(item["title"]).split())
        if words and any(len(words & prior) / max(1, len(words | prior)) >= 0.72 for prior in seen_words):
            continue
        seen_links.add(link)
        seen_words.append(words)
        item["link"] = link
        result.append(item)
    return result


def collect_news(config: dict[str, Any] | None = None, posted: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    config = config or load_config()
    posted = posted if posted is not None else json.loads((ROOT / "state/posted.json").read_text(encoding="utf-8"))
    known = {row.get("link", "").split("#", 1)[0].rstrip("/") for row in posted}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=int(config["lookback_hours"]))
    candidates: list[dict[str, Any]] = []

    for feed in config["feeds"]:
        try:
            parsed = feedparser.parse(feed["url"], request_headers={"User-Agent": "TajikistanRoadNewsBot/1.0"})
            if parsed.bozo and not parsed.entries:
                log.warning("Не удалось прочитать RSS %s: %s", feed["name"], parsed.bozo_exception)
                continue
            for entry in parsed.entries:
                published = _parse_date(entry)
                title = _clean_html(entry.get("title", ""))
                link = entry.get("link", "").strip()
                summary = _clean_html(entry.get("summary", entry.get("description", "")))[:1500]
                if not title or not link or not published or published < cutoff:
                    continue
                item = {"title": title, "summary": summary, "link": link, "source": feed["name"], "published": published.isoformat()}
                if item["link"].split("#", 1)[0].rstrip("/") not in known and _relevant(item, config):
                    item["id"] = hashlib.sha256(item["link"].encode()).hexdigest()[:16]
                    candidates.append(item)
        except Exception as exc:
            log.warning("Ошибка RSS %s: %s", feed["name"], exc)

    candidates.sort(key=lambda row: row["published"], reverse=True)
    return _dedupe(candidates)[: int(config["posts_per_run"])]

