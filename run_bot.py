from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.instagram import publish_carousel
from src.llm import _load_local_env, make_carousel
from src.news import collect_news, load_config
from src.render import render_slides


def main() -> int:
    parser = argparse.ArgumentParser(description="Сбор и публикация новостей о дорогах Таджикистана")
    parser.add_argument("--dry-run", action="store_true", help="собрать новости и карусели без публикации")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _load_local_env()
    config = load_config()
    posted_path = ROOT / "state/posted.json"
    posted = json.loads(posted_path.read_text(encoding="utf-8"))
    items = collect_news(config, posted)
    if not items:
        logging.info("Новых подходящих новостей нет")
        return 0
    for item in items:
        carousel = make_carousel(item, config)
        files = render_slides(carousel, item)
        caption = f"{carousel['caption']}\n\nИсточник: {item['source']} — {item['link']}"
        hashtags = " ".join(f"#{tag}" for tag in carousel["hashtags"])
        if hashtags:
            caption += f"\n\n{hashtags}"
        if args.dry_run:
            logging.info("DRY RUN: готова карусель %s (%d слайдов)", item["title"], len(files))
            print(json.dumps({"news": item, "carousel": carousel, "files": files}, ensure_ascii=False, indent=2))
            continue
        media_id = publish_carousel(files, caption, config["pages_base_url"])
        posted.append({"id": item["id"], "link": item["link"], "title": item["title"], "published_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "instagram_media_id": media_id})
        logging.info("Опубликовано: %s (Instagram ID %s)", item["title"], media_id)
    posted_path.write_text(json.dumps(posted[-500:], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        logging.exception("Работа бота завершилась ошибкой: %s", exc)
        raise SystemExit(1)
