from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class LLMError(RuntimeError):
    pass


def _load_local_env() -> None:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def _validate(data: Any, config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise LLMError("Ответ Gemini должен быть JSON-объектом")
    slides = data.get("slides")
    caption = data.get("caption")
    hashtags = data.get("hashtags", [])
    if not isinstance(slides, list) or not 3 <= len(slides) <= int(config["max_slides"]):
        raise LLMError("Ожидалось от 3 до 5 слайдов")
    if any(not isinstance(s, str) or not s.strip() or len(s) > int(config["max_slide_chars"]) for s in slides):
        raise LLMError("Пустой текст или превышена длина слайда")
    if not isinstance(caption, str) or not isinstance(hashtags, list):
        raise LLMError("Неверные поля подписи или хэштегов")
    return {"slides": slides, "caption": caption.strip(), "hashtags": [str(tag).lstrip("#") for tag in hashtags[:8]]}


def make_carousel(item: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    _load_local_env()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise LLMError("Не задан GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL") or "gemini-3.8-flash"
    prompt = f"""Подготовь русскоязычную карусель Instagram о подтверждённой новости. Регион: {config['region']}.
Стиль: {config['style']}
Используй только факты из переданных заголовка и описания. Ничего не додумывай. Если в источнике данных мало, напиши кратко и осторожно. Не выдавай предположение за установленный факт. Не копируй формулировки дословно.
Верни только JSON вида {{"slides":["...","...","..."],"caption":"...","hashtags":["..."]}}.
Сделай от 3 до {config['max_slides']} слайдов, до {config['max_slide_chars']} символов каждый. Первый слайд — ясный заголовок, последний — ссылка на источник в подписи, а не в тексте карточки.
Новость:
Заголовок: {item['title']}
Описание: {item['summary']}
Источник: {item['source']}
Ссылка: {item['link']}"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3}}
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
        text = result["candidates"][0]["content"]["parts"][0]["text"]
        return _validate(json.loads(text), config)
    except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError, LLMError) as exc:
        raise LLMError(f"Gemini не подготовил корректную карусель: {exc}") from exc

