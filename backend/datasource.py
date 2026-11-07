"""Загрузка наборов из API организаторов и получение ссылки на аудио звонка."""
from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request

UA = "cqa-prototype/1.0"


def api_url(group_id: str) -> str:
    base = os.environ.get("DATA_API_URL", "").strip()
    if not base:
        raise RuntimeError("Не задан DATA_API_URL в .env (ссылка GET из задания, без параметра group_id или с ним)")
    parts = urllib.parse.urlsplit(base)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if k != "group_id"]
    q.append(("group_id", group_id))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


def fetch_group(group_id: str) -> list[dict]:
    req = urllib.request.Request(api_url(group_id), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not payload.get("isSuccess"):
        raise RuntimeError(f"API вернул ошибку: {payload.get('error') or 'isSuccess=false'}")
    return payload["data"]["records"]


_SRC_RE = re.compile(r'<source[^>]+src="([^"]+)"', re.I)


def resolve_audio(audio_url: str) -> str | None:
    """Ссылка из набора открывает HTML-плеер; внутри — временная ссылка на mp3 (~1 час).
    Достаём её каждый раз заново."""
    req = urllib.request.Request(audio_url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if ctype.startswith("audio/"):
            return audio_url
        body = resp.read().decode("utf-8", "replace")
    m = _SRC_RE.search(body)
    return html.unescape(m.group(1)) if m else None
