"""Оценка диалога по чек-листу.

Режимы:
- LLM (Claude): по умолчанию, если доступен ключ ANTHROPIC_API_KEY.
- ollama: локальная модель через Ollama (CQA_EVALUATOR=ollama, CQA_MODEL=qwen2.5:3b, OLLAMA_URL).
- heuristic: демо-режим без модели (ключевые слова), чтобы интерфейс работал без ключа.

Модель только классифицирует (ответ/ступень, объяснение, номера реплик).
Баллы, перераспределение весов и итог считает scoring.py.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from scoring import NA, NO, SCORE, YES, normalize_item

SPEAKER = {"client": "КЛИЕНТ", "operator": "ОПЕРАТОР", "bot": "БОТ", "system": "СИСТЕМА"}

# $ за 1M токенов: (input, output). Кеш: чтение 0.1x, запись 1.25x от input.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

GUIDE = """Общие правила оценки:
- Оценивается только работа ОПЕРАТОРА (живого сотрудника). Реплики БОТА и СИСТЕМЫ — контекст: они не засчитываются оператору ни в плюс, ни в минус, но помогают понять ситуацию (например, бот уже собрал детали запроса, система зафиксировала эскалацию или закрытие чата).
- «Нет» — действие было нужно, но оператор его пропустил или выполнил неправильно. «N/A» — критерий неприменим по своему условию неприменимости. N/A ставь только если он разрешён для критерия и условие выполнено.
- Один и тот же чек-лист применяется к диалогам на русском, казахском и английском. Оценивай по смыслу, независимо от языка; «на языке клиента» означает язык, на котором пишет/говорит клиент.
- Для каждого критерия укажи номера реплик-доказательств (#N из транскрипта). Если действие пропущено, укажи проверенный участок (checked_from..checked_to) — где действие ожидалось. Для N/A объясни, какое условие неприменимости выполнено.
- Консультация оценивает, правильно и полно ли решён запрос. Ведение диалога оценивает, насколько понятно и последовательно оператор общался — это разные критерии.
- Для звонков транскрипт получен автоматическим распознаванием речи: возможны искажения слов. Не наказывай оператора за явные ошибки распознавания.
- Объяснения пиши кратко, 1–2 предложения, по-русски. Пример: «Завершение — Нет. После ответа на вопрос оператор закрыл чат без прощания. Основание — последняя реплика оператора и сообщение о закрытии чата»."""


def render_checklist(checklist: dict) -> str:
    lines = []
    for c in checklist["criteria"]:
        t = c["type"]
        if t == "binary":
            kind = "ДА/НЕТ" + ("/N/A" if c.get("na_allowed") else "")
            answers = "yes | no" + (" | na" if c.get("na_allowed") else "")
        elif t == "scale":
            steps = ", ".join(str(s["value"]) for s in c["scale"])
            kind = f"ШКАЛА {steps}" + (" / N/A" if c.get("na_allowed") else "")
            answers = "score (+ поле score = одна из ступеней)" + (" | na" if c.get("na_allowed") else "")
        else:
            kind = "ФЛАГ ГРУБОЙ ОШИБКИ (без баллов): yes = нарушение выявлено"
            answers = "yes | no"
        lines.append(f"[{c['id']}] {c.get('code', '')} {c['title']} — {kind}")
        lines.append(f"  Правило: {c.get('description', '')}")
        if c.get("na_allowed") and c.get("na_condition"):
            lines.append(f"  N/A допустим, если: {c['na_condition']}")
        if t == "scale":
            for s in c["scale"]:
                lines.append(f"    {s['value']} — {s['label']}")
        lines.append(f"  Допустимые answer: {answers}")
    return "\n".join(lines)


def render_transcript(dialog: dict) -> str:
    out = [f"Канал: {dialog.get('channel')}; язык набора: {dialog.get('language')}"]
    for i, m in enumerate(dialog["transcript"], start=1):
        out.append(f"#{i} [{m.get('timestamp', '')}] {SPEAKER.get(m.get('speaker'), m.get('speaker', '').upper())}: {m.get('text', '')}")
    return "\n".join(out)


def output_schema(checklist: dict) -> dict:
    ids = [c["id"] for c in checklist["criteria"]]
    return {
        "type": "object",
        "properties": {
            "criteria": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion_id": {"type": "string", "enum": ids},
                        "answer": {"type": "string", "enum": [YES, NO, NA, SCORE]},
                        "score": {"type": "integer", "description": "Ступень шкалы для answer=score, иначе 0"},
                        "explanation": {"type": "string"},
                        "evidence": {"type": "array", "items": {"type": "integer"}, "description": "Номера реплик #N"},
                        "checked_from": {"type": "integer", "description": "Начало проверенного участка (#N) или 0"},
                        "checked_to": {"type": "integer", "description": "Конец проверенного участка (#N) или 0"},
                        "na_reason": {"type": "string", "description": "Причина N/A или пустая строка"},
                    },
                    "required": ["criterion_id", "answer", "score", "explanation", "evidence",
                                 "checked_from", "checked_to", "na_reason"],
                    "additionalProperties": False,
                },
            },
            "summary": {"type": "string", "description": "Краткое резюме работы оператора, 1–2 предложения"},
        },
        "required": ["criteria", "summary"],
        "additionalProperties": False,
    }


def mode() -> str:
    """claude | ollama | heuristic"""
    m = os.environ.get("CQA_EVALUATOR", "").lower()
    if m in ("heuristic", "ollama"):
        return m
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "claude"
    return "heuristic"


def llm_available() -> bool:
    return mode() != "heuristic"


def model_name() -> str:
    default = "qwen2.5:3b" if mode() == "ollama" else "claude-opus-5-5"
    return os.environ.get("CQA_MODEL", default)


_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(max_retries=4)
    return _client


def _cost(model: str, usage) -> float | None:
    p = PRICES.get(model)
    if not p:
        return None
    pin, pout = p
    inp = usage.input_tokens or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    out = usage.output_tokens or 0
    return round((inp * pin + cw * pin * 1.25 + cr * pin * 0.1 + out * pout) / 1_000_000, 5)


def evaluate_llm(dialog: dict, checklist: dict) -> tuple[dict, dict]:
    client = _get_client()
    model = model_name()
    system = [
        {"type": "text", "text": "Ты — эксперт по контролю качества клиентской поддержки брокерской компании Freedom Broker. "
                                 "Оцени работу оператора по чек-листу строго по его правилам.\n\n" + GUIDE},
        {"type": "text", "text": "ЧЕК-ЛИСТ:\n" + render_checklist(checklist), "cache_control": {"type": "ephemeral"}},
    ]
    user = ("Оцени диалог ниже по каждому критерию чек-листа (ровно один элемент на каждый criterion_id).\n\n"
            "ТРАНСКРИПТ:\n" + render_transcript(dialog))
    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_config={"effort": os.environ.get("CQA_EFFORT", "medium"),
                       "format": {"type": "json_schema", "schema": output_schema(checklist)}},
    )
    if os.environ.get("CQA_FALLBACKS", "1") == "1" and model in ("claude-opus-5-5", "claude-sonnet-5-5"):
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
    else:
        resp = client.messages.create(**kwargs)

    if resp.stop_reason == "refusal":
        raise RuntimeError("Модель отказалась оценивать диалог (refusal)")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Ответ модели обрезан (max_tokens)")
    text = next(b.text for b in resp.content if b.type == "text")
    data = json.loads(text)
    usage = {
        "model": resp.model,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "cache_read_input_tokens": getattr(resp.usage, "cache_read_input_tokens", 0),
        "cache_creation_input_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0),
        "cost_usd": _cost(model, resp.usage),
        "calls": 1,
    }
    return data, usage


def evaluate_ollama(dialog: dict, checklist: dict) -> tuple[dict, dict]:
    """Локальная модель через Ollama /api/chat со структурированным выводом (format = JSON-схема)."""
    import urllib.request

    model = model_name()
    # По умолчанию — по одному критерию за запрос (надёжнее для маленьких моделей), см. ollama_eval.py.
    # OLLAMA_PER_CRITERION=0 — старый режим: все критерии одним запросом.
    if os.environ.get("OLLAMA_PER_CRITERION", "1") == "1":
        from ollama_eval import evaluate_per_criterion
        return evaluate_per_criterion(dialog, checklist, model)
    url = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/") + "/api/chat"
    system = ("Ты — эксперт по контролю качества клиентской поддержки брокерской компании Freedom Broker. "
              "Оцени работу оператора по чек-листу строго по его правилам.\n\n" + GUIDE
              + "\n\nЧЕК-ЛИСТ:\n" + render_checklist(checklist))
    user = ("Оцени диалог ниже по каждому критерию чек-листа (ровно один элемент на каждый criterion_id). "
            "Ответь только JSON по схеме.\n\nТРАНСКРИПТ:\n" + render_transcript(dialog))
    body = {
        "model": model,
        "stream": False,
        "format": output_schema(checklist),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        # Контекст по умолчанию у Ollama маленький — чек-лист + транскрипт иначе обрежутся.
        "options": {"temperature": 0, "num_ctx": int(os.environ.get("OLLAMA_NUM_CTX", "16384"))},
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=int(os.environ.get("OLLAMA_TIMEOUT", "600"))) as r:
        resp = json.loads(r.read().decode("utf-8"))
    data = json.loads(resp["message"]["content"])
    usage = {
        "model": f"ollama/{model}",
        "input_tokens": resp.get("prompt_eval_count", 0),
        "output_tokens": resp.get("eval_count", 0),
        "cost_usd": 0,
        "calls": 1,
    }
    return data, usage


# ---------------- Эвристический демо-режим ----------------

GREET = r"здравствуй|добрый (день|вечер)|доброе утро|приветству|сәлем|қайырлы|hello|\bhi\b|good (morning|afternoon|evening)|welcome"
INTRO = r"меня зовут|менің атым|атым|my name is|this is \w+|i am \w+|i'm \w+|на связи"
BYE = r"до свидания|всего доброго|хорошего (дня|вечера)|сау болыңыз|қош|goodbye|have a (nice|great|good)|bye"
IDENT = r"кодов\w* слов|дат\w* рождения|идентифик|верифик|подтвердите личность|туған күн|растау|verify|verification|date of birth|identity|security question"
RUDE = r"идиот|дурак|тупой|stupid|idiot|ақымақ"
BYPASS = r"(переведите|переведи|transfer).{0,40}(мне лично|на мою карту|to me personally|my personal)|личн\w* номер|personal (phone|number)"


def evaluate_heuristic(dialog: dict, checklist: dict) -> tuple[dict, dict]:
    tr = dialog["transcript"]
    ops = [i for i, m in enumerate(tr, start=1) if m.get("speaker") == "operator"]
    txt = lambda i: (tr[i - 1].get("text") or "").lower()  # noqa: E731
    res = []

    def put(cid, answer, expl, ev=(), score=0, rng=(0, 0), na=""):
        res.append({"criterion_id": cid, "answer": answer, "score": score, "explanation": expl,
                    "evidence": list(ev), "checked_from": rng[0], "checked_to": rng[1], "na_reason": na})

    first, last = (ops[0], ops[-1]) if ops else (0, 0)
    for c in checklist["criteria"]:
        cid, t = c["id"], c["type"]
        if not ops:
            if c.get("na_allowed"):
                put(cid, NA, "Оператор не участвовал в диалоге.", na="Нет реплик оператора.")
            else:
                put(cid, NO if t != "scale" else SCORE, "Оператор не участвовал в диалоге.", rng=(1, len(tr)))
            continue
        title = c["title"].lower()
        if "контакт" in title:
            ok = re.search(GREET, txt(first)) and re.search(INTRO, txt(first))
            put(cid, YES if ok else NO, "Приветствие и представление в первом ответе " + ("найдены." if ok else "не найдены."),
                ev=[first], rng=(first, first))
        elif "потребност" in title:
            q = [i for i in ops if "?" in txt(i)]
            if q:
                put(cid, YES, "Оператор задавал уточняющие вопросы.", ev=q[:2])
            elif c.get("na_allowed"):
                put(cid, NA, "Уточнений не было; вероятно, данных хватало.", na="Данных для ответа хватало с самого начала.")
            else:
                put(cid, NO, "Уточняющих вопросов нет.", rng=(first, last))
        elif "идентиф" in title:
            hit = [i for i in ops if re.search(IDENT, txt(i))]
            if hit:
                put(cid, YES, "Найдена проверка личности.", ev=hit[:2])
            elif c.get("na_allowed"):
                put(cid, NA, "Признаков защищённого действия не найдено.", na="Запрос не требовал идентификации.")
            else:
                put(cid, NO, "Проверка личности не найдена.", rng=(first, last))
        elif "грубы" in title or t == "flag":
            hit = [i for i in ops if re.search(RUDE, txt(i)) or re.search(BYPASS, txt(i))]
            put(cid, YES if hit else NO, "Найдены признаки грубой ошибки." if hit else "Грубых ошибок не выявлено.",
                ev=hit, rng=(first, last))
        elif "заверш" in title:
            ok = re.search(BYE, txt(last))
            put(cid, YES if ok else NO, "Оператор " + ("попрощался." if ok else "не попрощался в последней реплике."),
                ev=[last], rng=(last, last))
        elif t == "scale":
            steps = sorted(s["value"] for s in c["scale"])
            v = steps[-2] if len(steps) > 1 else steps[-1]
            put(cid, SCORE, "Демо-режим: средне-высокая оценка без анализа смысла.", ev=[last], score=v)
        else:
            put(cid, YES, "Демо-режим: явных нарушений не найдено.", ev=[first])
    return ({"criteria": res, "summary": "Демо-оценка по ключевым словам (без модели)."},
            {"model": "heuristic", "cost_usd": 0, "calls": 0})


def evaluate(dialog: dict, checklist: dict) -> tuple[dict[str, dict], str, dict, str]:
    """Возвращает (items, evaluator_name, usage, summary). items нормализованы."""
    m = mode()
    if m == "ollama":
        data, usage = evaluate_ollama(dialog, checklist)
        name = usage["model"]
    elif m == "claude":
        data, usage = evaluate_llm(dialog, checklist)
        name = usage.get("model") or model_name()
    else:
        data, usage = evaluate_heuristic(dialog, checklist)
        name = "heuristic"

    n = len(dialog["transcript"])
    by_id = {c["id"]: c for c in checklist["criteria"]}
    items: dict[str, dict] = {}
    for r in data.get("criteria", []):
        c = by_id.get(r.get("criterion_id"))
        if not c or c["id"] in items:
            continue
        it, warnings = normalize_item(c, r)
        it["evidence"] = [e for e in (r.get("evidence") or []) if 1 <= e <= n]
        cf, ct = r.get("checked_from") or 0, r.get("checked_to") or 0
        it["checked_range"] = [cf, ct] if 1 <= cf <= ct <= n else None
        it["warnings"] = warnings
        items[c["id"]] = it
    for cid, c in by_id.items():
        if cid not in items:
            items[cid] = {"answer": NO if c["type"] != "scale" else SCORE, "score": 0 if c["type"] == "scale" else None,
                          "explanation": "Модель не вернула оценку по этому критерию — требуется ручная проверка.",
                          "evidence": [], "checked_range": None, "na_reason": "",
                          "warnings": ["Нет ответа модели"]}
    return items, name, usage, data.get("summary", "")
