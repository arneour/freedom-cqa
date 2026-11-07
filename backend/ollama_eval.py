"""Оценка маленькой локальной моделью (Ollama): по одному критерию за запрос.

Почему так:
- Модели на 3–4 млрд параметров путаются, когда нужно оценить все критерии сразу,
  и зацикливаются на длинном JSON. Один критерий + узкая схема (enum ответов,
  ограничение длины текста) резко снижает ошибки.
- Транскрипт и факты стоят в начале запроса и одинаковы для всех критериев —
  Ollama переиспользует уже посчитанный префикс, поэтому 8 запросов почти не дороже одного.
- Простые факты (где реплики оператора, кто написал последним) считает код и
  передаёт модели как подсказку.
"""
from __future__ import annotations

import json
import os
import urllib.request

from evaluator import SPEAKER, render_transcript
from rules import apply_rule, is_verdict

SYSTEM = (
    "Ты проверяешь качество работы ОПЕРАТОРА (живого сотрудника) в диалоге поддержки брокера Freedom Broker. "
    "Реплики БОТА и СИСТЕМЫ — только контекст: это не оператор, их действия оператору не засчитываются. "
    "Диалог может быть на русском, казахском или английском — оценивай по смыслу. "
    "«Нет» — действие было нужно, но оператор его не выполнил. «N/A» — только если выполнено условие неприменимости. "
    "Сначала кратко объясни решение (1–2 предложения по-русски, со ссылкой на номер реплики #N), затем дай ответ. "
    "Отвечай только JSON по схеме."
)


def _facts(dialog: dict) -> str:
    tr = dialog["transcript"]
    ops = [i for i, m in enumerate(tr, start=1) if m.get("speaker") == "operator"]
    short = lambda i: (tr[i - 1].get("text") or "").replace("\n", " ")[:200]  # noqa: E731
    lines = ["ФАКТЫ (посчитаны автоматически, им можно доверять):"]
    if not ops:
        lines.append("- Оператор в диалоге не писал ни одной реплики.")
    else:
        lines.append(f"- Реплики оператора: {', '.join('#' + str(i) for i in ops)}")
        lines.append(f"- Первая реплика оператора #{ops[0]}: «{short(ops[0])}»")
        lines.append(f"- Последняя реплика оператора #{ops[-1]}: «{short(ops[-1])}»")
        cl = [i for i, m in enumerate(tr, start=1) if m.get("speaker") == "client"]
        if cl:
            lines.append(f"- Первая реплика клиента #{cl[0]}: «{short(cl[0])}»")
    last = tr[-1]
    lines.append(f"- Последняя реплика диалога #{len(tr)} ({SPEAKER.get(last.get('speaker'), last.get('speaker'))}): «{short(len(tr))}»")
    return "\n".join(lines)


def _criterion_prompt(c: dict, no_na: bool = False) -> str:
    na_ok = c.get("na_allowed") and not no_na
    out = [f"КРИТЕРИЙ {c.get('code', '')} «{c['title']}»", f"Правило: {c.get('description', '')}"]
    if c["type"] == "scale":
        out.append("Выбери одну ступень шкалы (поле score):")
        out += [f"  {s['value']} — {s['label']}" for s in c["scale"]]
        out.append("Выбери ступень, описание которой точнее всего подходит. Если конкретных проблем нет — ставь верхнюю "
                   "ступень. Ниже верхней — только при конкретной проблеме, назови её и номер реплики.")
        out.append('Поле answer = "score".' + (' Или answer = "na", если выполнено условие N/A.' if na_ok else ""))
    elif c["type"] == "flag":
        out.append('answer = "yes", если нарушение ЕСТЬ; "no", если нарушения нет. Поле score = 0.')
    else:
        out.append('answer = "yes" или "no"' + (' или "na"' if na_ok else "") + ". Поле score = 0.")
        if na_ok:
            out.append("Если действие в этом диалоге не требовалось — это N/A, а не «Нет». "
                       "«Нет» — только если действие было нужно, но оператор его не сделал.")
    if no_na:
        out.append("Клиент сформулировал запрос — N/A здесь недопустим.")
    if na_ok and c.get("na_condition"):
        out.append(f"Условие N/A: {c['na_condition']}")
        out.append("Сначала реши na_condition_met: true, если это условие выполнено (тогда answer = \"na\"), иначе false.")
    if c["type"] == "flag":
        out.append("violation_quote — ТОЧНАЯ цитата из реплики ОПЕРАТОРА, где есть нарушение, или пустая строка. "
                   "Без цитаты нарушения нет. Обычная вежливая консультация нарушением не является.")
    out.append("evidence — номера реплик-доказательств (до 3). Если действие пропущено — checked_from/checked_to: "
               "участок, где оно ожидалось, иначе 0. na_reason — причина N/A или пустая строка.")
    return "\n".join(out)


def _schema(c: dict, no_na: bool = False) -> dict:
    na_ok = c.get("na_allowed") and not no_na
    if c["type"] == "scale":
        answers = ["score"] + (["na"] if na_ok else [])
        scores = sorted({s["value"] for s in c["scale"]})
    else:
        answers = ["yes", "no"] + (["na"] if na_ok and c["type"] != "flag" else [])
        scores = [0]
    props: dict = {"explanation": {"type": "string", "maxLength": 300}}
    if c["type"] == "flag":
        props["violation_quote"] = {"type": "string", "maxLength": 200}
    if "na" in answers:
        props["na_condition_met"] = {"type": "boolean"}
    return {
        "type": "object",
        "properties": {
            **props,
            "answer": {"type": "string", "enum": answers},
            "score": {"type": "integer", "enum": scores},
            "evidence": {"type": "array", "items": {"type": "integer"}, "maxItems": 3},
            "checked_from": {"type": "integer"},
            "checked_to": {"type": "integer"},
            "na_reason": {"type": "string", "maxLength": 200},
        },
        "required": [*props, "answer", "score", "evidence", "checked_from", "checked_to", "na_reason"],
    }


def _norm(s: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in s).split())


def _postprocess(c: dict, r: dict, dialog: dict) -> dict:
    """Проверки кодом поверх ответа маленькой модели."""
    if "na_condition_met" in r:
        if r["na_condition_met"] and r.get("answer") != "na":
            r["answer"] = "na"
            r["na_reason"] = r.get("na_reason") or c.get("na_condition", "")
        elif not r["na_condition_met"] and r.get("answer") == "na":
            r["answer"] = "score" if c["type"] == "scale" else "no"
    if c["type"] == "flag" and r.get("answer") == "yes":
        quote = _norm(r.get("violation_quote") or "")
        ops = [(i, _norm(m.get("text") or "")) for i, m in enumerate(dialog["transcript"], start=1)
               if m.get("speaker") == "operator"]
        hit = [i for i, t in ops if len(quote) >= 8 and quote in t]
        if hit:
            r["evidence"] = hit[:3]
        else:
            r["answer"] = "no"
            r["evidence"] = []
            r["explanation"] = ("Нарушение не подтверждено: модель не привела точную цитату оператора. "
                                "Исходный вывод модели: " + (r.get("explanation") or ""))[:400]
    return r


def _num_ctx(messages: list[dict]) -> int:
    """Контекст под длину запроса: иначе Ollama молча обрежет начало транскрипта.
    Оценка сверху: ~1 токен на 1.6 символа (замер qwen2.5 на русском звонке — 1.7) + запас на ответ."""
    base = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))
    need = int(sum(len(m["content"]) for m in messages) / 1.6) + 600
    ctx = base
    while ctx < need and ctx < 32768:
        ctx *= 2
    return ctx


def _chat(model: str, messages: list[dict], schema: dict) -> dict:
    url = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/") + "/api/chat"
    body = {
        "model": model, "stream": False, "format": schema, "messages": messages,
        "keep_alive": "30m",
        "options": {"temperature": 0, "num_ctx": _num_ctx(messages),
                    "num_predict": 400, "repeat_penalty": 1.15},
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=int(os.environ.get("OLLAMA_TIMEOUT", "300"))) as r:
        return json.loads(r.read().decode("utf-8"))


def evaluate_per_criterion(dialog: dict, checklist: dict, model: str) -> tuple[dict, dict]:
    # 1) Формальные критерии — правилами (rules.py). 2) Остальные — моделью, с вердиктами правил как фактами.
    ruled = {c["id"]: apply_rule(dialog, c) for c in checklist["criteria"]}
    labels = {"yes": "Да", "no": "Нет", "na": "N/A"}
    rule_facts = [f"- {c.get('code', '')} {c['title']}: {labels.get(r['answer'], r['answer'])}. {r['explanation']}"
                  for c in checklist["criteria"] if is_verdict(r := ruled[c["id"]])]
    head = ("ТРАНСКРИПТ:\n" + render_transcript(dialog) + "\n\n" + _facts(dialog) + "\n"
            + ("Уже проверено правилами:\n" + "\n".join(rule_facts) + "\n" if rule_facts else "") + "\n")
    results, tin, tout = [r for r in ruled.values() if is_verdict(r)], 0, 0
    calls = 0
    for c in checklist["criteria"]:
        if is_verdict(ruled[c["id"]]):
            continue
        no_na = (ruled[c["id"]] or {}).get("constraint") == "no_na"
        calls += 1
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": head + _criterion_prompt(c, no_na)}]
        resp = _chat(model, messages, _schema(c, no_na))
        tin += resp.get("prompt_eval_count", 0) or 0
        tout += resp.get("eval_count", 0) or 0
        try:
            r = json.loads(resp["message"]["content"])
        except json.JSONDecodeError:
            # Обрезанный ответ — пропускаем; evaluate() пометит критерий «требуется ручная проверка».
            continue
        r["criterion_id"] = c["id"]
        r["source"] = "model"
        results.append(_postprocess(c, r, dialog))
    usage = {"model": f"ollama/{model}", "input_tokens": tin, "output_tokens": tout, "cost_usd": 0,
             "calls": calls, "rule_criteria": len(checklist["criteria"]) - calls}
    return {"criteria": results, "summary": ""}, usage
