om future import annotations

import hashlib
import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import feedparser

ROOT = Path(file).resolve().parents[1]
log = logging.getLogger(name)

def load_config() -> dict[str, Any]:
return json.loads(
(ROOT / "config.json").read_text(encoding="utf-8")
)

def _clean_html(value: str) -> str:
value = html.unescape(value or "")
value = re.sub(r"<[^>]+>", " ", value)

value = re.sub(
    r"(запись .*? впервые появилась|"
    r"впервые появилась на сайте|"
    r"подписывайтесь на|читать далее|читайте также).*",
    " ",
    value,
    flags=re.IGNORECASE,
)

return re.sub(r"\s+", " ", value).strip()
def _parse_date(entry: Any) -> datetime | None:
for key in (
"published_parsed",
"updated_parsed",
"created_parsed",
):
value = entry.get(key)

    if value:
        import calendar

        return datetime.fromtimestamp(
            calendar.timegm(value),
            tz=timezone.utc,
        )

return None
def _normalise(text: str) -> str:
text = html.unescape(text or "").casefold()
return re.sub(
r"[^\w\s]",
" ",
text,
flags=re.UNICODE,
)

def _contains_any(text: str, terms: list[str]) -> bool:
text = text.casefold()
return any(
term.casefold() in text
for term in terms
)

def _relevant(
item: dict[str, Any],
config: dict[str, Any],
) -> bool:

title = item["title"].casefold()
summary = item["summary"].casefold()
text = f"{title} {summary}"

topic_terms = [
    "дтп",
    "авари",
    "столкнов",
    "сбил",
    "сбит",
    "наезд",
    "погиб",
    "пострад",
    "водител",
    "автомоб",
    "машин",
    "такси",
    "транспорт",
    "автобус",
    "маршрут",
    "грузов",
    "фур",
    "трасс",
    "дорог",
    "дорожн",
    "движен",
    "перекрыт",
    "пробк",
    "гаи",
    "пдд",
    "штраф",
    "топлив",
    "бензин",
    "дизел",
    "электромоб",
    "зарядн",
    "автосалон",
    "автосервис",
    "запчаст",
    "шина",
    "парков",
    "светофор",
]

region_terms = [
    "таджикистан",
    "таджик",
    "душанбе",
    "худжанд",
    "бохтар",
    "куляб",
    "кулоб",
    "хорог",
    "гиссар",
    "ҳисор",
    "вахдат",
    "согд",
    "хатлон",
    "гбао",
    "памир",
    "чанак",
]

if not _contains_any(text, topic_terms):
    return False

if not _contains_any(text, region_terms):
    return False

title_has_topic = _contains_any(
    title,
    topic_terms,
)

if not title_has_topic:
    strong_terms = [
        "дтп",
        "авари",
        "столкнов",
        "сбил",
        "сбит",
        "наезд",
        "дорог",
        "дорожн",
        "движен",
        "перекрыт",
        "такси",
        "автомоб",
        "машин",
        "водител",
        "транспорт",
        "электромоб",
        "бензин",
        "топлив",
    ]

    if not _contains_any(text, strong_terms):
        return False

return True
def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
seen_links: set[str] = set()
seen_words: list[set[str]] = []
result = []

for item in items:
    link = item["link"].split("#", 1)[0].rstrip("/")

    if link in seen_links:
        continue

    words = set(_normalise(item["title"]).split())

    if words and any(
        len(words & prior) / max(1, len(words | prior)) >= 0.72
        for prior in seen_words
    ):
        continue

    seen_links.add(link)
    seen_words.append(words)
    item["link"] = link
    result.append(item)

return result
def collect_news(
config: dict[str, Any] | None = None,
posted: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:

config = config or load_config()

if posted is None:
    posted = json.loads(
        (ROOT / "state/posted.json").read_text(
            encoding="utf-8"
        )
    )

known = {
    row.get("link", "").split("#", 1)[0].rstrip("/")
    for row in posted
}

cutoff = datetime.now(timezone.utc) - timedelta(
    hours=int(config["lookback_hours"])
)

candidates: list[dict[str, Any]] = []

for feed in config["feeds"]:
    try:
        parsed = feedparser.parse(
            feed["url"],
            request_headers={
                "User-Agent": "TajikistanRoadNewsBot/1.0"
            },
        )

        if parsed.bozo and not parsed.entries:
            log.warning(
                "Не удалось прочитать RSS %s: %s",
                feed["name"],
                parsed.bozo_exception,
            )
            continue

        for entry in parsed.entries:
            published = _parse_date(entry)

            title = _clean_html(
                entry.get("title", "")
            )

            link = entry.get("link", "").strip()

            summary = _clean_html(
                entry.get(
                    "summary",
                    entry.get("description", ""),
                )
            )[:1500]

            if not title or not link or not published:
                continue

            if published < cutoff:
                continue

            item = {
                "title": title,
                "summary": summary,
                "link": link,
                "source": feed["name"],
                "published": published.isoformat(),
            }

            clean_link = (
                link.split("#", 1)[0].rstrip("/")
            )

            if clean_link in known:
                continue

            if not _relevant(item, config):
                continue

            item["id"] = hashlib.sha256(
                clean_link.encode()
            ).hexdigest()[:16]

            candidates.append(item)

    except Exception as exc:
        log.warning(
            "Ошибка RSS %s: %s",
            feed["name"],
            exc,
        )

candidates.sort(
    key=lambda row: row["published"],
    reverse=True,
)

result = _dedupe(candidates)

log.info(
    "Найдено подходящих новостей: %d",
    len(result),
)

for item in result:
    log.info(
        "Подходящая новость: [%s] %s",
        item["source"],
        item["title"],
    )

return result[: int(config["posts_per_run"])]
