from __future__ import annotations

import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Any


class LLMError(RuntimeError):
    pass


TIMEOUT = 45
RETRY_CODES = {408, 425, 429, 500, 502, 503, 504}
DELAYS = (3, 8)  # паузы между попытками на одной модели (секунды)
DEFAULT_GEMINI_MODELS = ["gemini-3.6-flash", "gemini-3.1-flash-lite", "gemini-3-flash-preview", "gemini-3.8-flash"]

def _load_local_env() -> None:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"\''))


def _clean_hashtags(raw: Any) -> list[str]:
    result: list[str] = []
    for tag in raw if isinstance(raw, list) else []:
        tag = "".join(str(tag).lstrip("#").split())  # убрать # и пробелы внутри
        if tag and tag.lower() not in [t.lower() for t in result]:
            result.append(tag)
    return result[:8]


def _validate(data: Any, config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise LLMError("Ответ должен быть JSON-объектом")
    slides = data.get("slides")
    caption = data.get("caption")
    if not isinstance(slides, list) or not 3 <= len(slides) <= int(config["max_slides"]):
        raise LLMError(f"Ожидалось от 3 до {config['max_slides']} слайдов")
    slides = [s.strip() if isinstance(s, str) else "" for s in slides]
    if any(not s or len(s) > int(config["max_slide_chars"]) for s in slides):
        raise LLMError("Пустой текст или превышена длина слайда")
    if not isinstance(caption, str) or not caption.strip():
        raise LLMError("Пустая подпись")
    if len(caption) > 1500:
        raise LLMError("Слишком длинная подпись")
    return {"slides": slides, "caption": caption.strip(), "hashtags": _clean_hashtags(data.get("hashtags", []))}


def _build_prompt(item: dict[str, Any], config: dict[str, Any]) -> str:
    return f"""Подготовь русскоязычную карусель Instagram о подтверждённой новости. Регион: {config['region']}.
Стиль: {config['style']}
Используй только факты из переданных заголовка и описания. Ничего не додумывай. Если в источнике данных мало, напиши кратко и осторожно. Не выдавай предположение за установленный факт. Не копируй формулировки дословно.
Верни только JSON вида {{"slides":["...","...","..."],"caption":"...","hashtags":["..."]}}.
Сделай от 3 до {config['max_slides']} слайдов, до {config['max_slide_chars']} символов каждый. Первый слайд — ясный заголовок, последний — короткий вывод. Ссылку на источник в слайды не вставляй: она добавляется в подпись автоматически.
Новость:
Заголовок: {item['title']}
Описание: {item['summary']}
Источник: {item['source']}
Ссылка: {item['link']}"""


def _post_json(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _call_gemini(model: str, key: str, prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3}}
    result = _post_json(url, body, {"x-goog-api-key": key})  # ключ в заголовке, не в ссылке
    return result["candidates"][0]["content"]["parts"][0]["text"]


def _call_openai_compat(url: str, model: str, key: str, prompt: str) -> str:
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}, "temperature": 0.3}
    result = _post_json(url, body, {"Authorization": f"Bearer {key}"})
    return result["choices"][0]["message"]["content"]


def _gemini_models() -> list[str]:
    models: list[str] = []
    custom = os.getenv("GEMINI_MODEL") or ""
    for name in [m.strip() for m in custom.split(",") if m.strip()] + DEFAULT_GEMINI_MODELS:
        if name not in models:
            models.append(name)
    return models


def _providers() -> list[tuple[str, str, str, str]]:
    """(тип, модель, ключ, адрес). Провайдер без ключа пропускается."""
    out: list[tuple[str, str, str, str]] = []
    key = os.getenv("GEMINI_API_KEY")
    if key:
        out += [("gemini", m, key, "") for m in _gemini_models()]
    key = os.getenv("GROQ_API_KEY")
    if key:
        out.append(("groq", os.getenv("GROQ_MODEL") or "llama-3.3-70b-versatile", key,
                    "https://api.groq.com/openai/v1/chat/completions"))
    key = os.getenv("MISTRAL_API_KEY")
    if key:
        out.append(("mistral", os.getenv("MISTRAL_MODEL") or "mistral-small-latest", key,
                    "https://api.mistral.ai/v1/chat/completions"))
    return out


def make_carousel(item: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    _load_local_env()
    providers = _providers()
    if not providers:
        raise LLMError("Не задан ни один ключ (GEMINI_API_KEY, GROQ_API_KEY, MISTRAL_API_KEY)")
    prompt = _build_prompt(item, config)
    errors: list[str] = []

    for kind, model, key, url in providers:
        label = f"{kind}/{model}"
        for attempt in range(1, len(DELAYS) + 2):
            can_retry = attempt <= len(DELAYS)
            try:
                if kind == "gemini":
                    text = _call_gemini(model, key, prompt)
                else:
                    text = _call_openai_compat(url, model, key, prompt)
                result = _validate(json.loads(text), config)
                logging.info("Текст подготовлен: %s (попытка %d)", label, attempt)
                return result
            except urllib.error.HTTPError as exc:
                errors.append(f"{label}: HTTP {exc.code}")
                logging.warning("%s: HTTP %s (попытка %d)", label, exc.code, attempt)
                if exc.code in RETRY_CODES and can_retry:
                    time.sleep(DELAYS[attempt - 1])
                    continue
                break  # 404/400/401/403: эта модель не подходит, берём следующую
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
                errors.append(f"{label}: сеть ({type(exc).__name__})")
                logging.warning("%s: сетевая ошибка %s (попытка %d)", label, type(exc).__name__, attempt)
                if can_retry:
                    time.sleep(DELAYS[attempt - 1])
                    continue
                break
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, LLMError) as exc:
                errors.append(f"{label}: плохой ответ ({exc})")
                logging.warning("%s: ответ не прошёл проверку: %s (попытка %d)", label, exc, attempt)
                if can_retry:
                    time.sleep(1)
                    continue
                break

    raise LLMError("Не удалось подготовить текст ни одной моделью: " + "; ".join(errors[-6:]))
