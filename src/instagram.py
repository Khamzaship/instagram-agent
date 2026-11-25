from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_VERSION = os.getenv("INSTAGRAM_API_VERSION", "v27.0")
BASE = f"https://graph.instagram.com/{API_VERSION}"


class InstagramError(RuntimeError):
    pass


def _post(path: str, values: dict[str, Any], token: str) -> dict[str, Any]:
    body = urllib.parse.urlencode({**values, "access_token": token}).encode()
    req = urllib.request.Request(f"{BASE}/{path.lstrip('/')}", data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        raise InstagramError(f"Ошибка Instagram API: {exc}") from exc
    if "error" in data:
        raise InstagramError(data["error"].get("message", "Instagram API вернул ошибку"))
    return data


def publish_carousel(paths: list[str], caption: str, pages_url: str) -> str:
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    user_id = os.getenv("INSTAGRAM_USER_ID")
    if not token or not user_id:
        raise InstagramError("Для публикации нужны INSTAGRAM_ACCESS_TOKEN и INSTAGRAM_USER_ID")
    children = []
    for name in paths:
        media_url = f"{pages_url.rstrip('/')}/media/{urllib.parse.quote(name)}"
        container = _post(f"{user_id}/media", {"image_url": media_url, "is_carousel_item": "true"}, token)
        children.append(container["id"])
    album = _post(f"{user_id}/media", {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption}, token)
    creation_id = album["id"]
    for _ in range(12):
        time.sleep(5)
        req = urllib.request.Request(f"{BASE}/{creation_id}?fields=status_code&access_token={urllib.parse.quote(token)}")
        with urllib.request.urlopen(req, timeout=30) as response:
            status = json.loads(response.read().decode("utf-8")).get("status_code")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise InstagramError("Instagram не смог обработать изображение карусели")
    else:
        raise InstagramError("Instagram не завершил обработку карусели за минуту")
    published = _post(f"{user_id}/media_publish", {"creation_id": creation_id}, token)
    return published["id"]

