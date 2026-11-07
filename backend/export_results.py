"""Выгрузка результатов в формате организаторов «Результаты оценки • Формат JSON 1.0».

Структура: {schema_version, team_name, model{mode,name}, results[{conversation_id, status, criteria[8], score}]}.
- value: true = «Да», false = «Нет», null = N/A; для шкал — число до учёта веса.
- evidence_turns — номера реплик с 1 по исходному массиву транскрипта (как у нас).
- final_score — итог 0–100 с перераспределением веса при N/A, округлён до двух знаков (scoring.compute).
- Неоценённый диалог — status "error", пустые criteria, score null, причина в error. Нулём не подменяется.

По умолчанию выгружается ИСХОДНАЯ оценка системы (без ручных правок по апелляциям): source="original".
source="final" — с учётом решений проверяющего.
"""
from __future__ import annotations

import json
import os
from typing import Any

from db import row, tx

SCHEMA_VERSION = "1.0"

# Наши ID критериев исходного чек-листа → коды формата организаторов
EXPORT_CODES = {
    "c01": "contact", "c02": "discovery", "c03": "identification", "c04": "consultation",
    "c05": "empathy", "c06": "dialogue", "c07": "closure", "c08": "critical",
}
CODE_ORDER = list(EXPORT_CODES.values())

# Допустимые значения по правилам заполнения
ALLOWED = {
    "contact": {True, False},
    "discovery": {True, False, None},
    "identification": {True, False, None},
    "consultation": {0, 5, 10, 15, 20, None},
    "empathy": {True, False},
    "dialogue": {0, 4, 7, 10},
    "closure": {True, False, None},
    "critical": {True, False},
}


def to_value(crit: dict, item: dict):
    ans = item.get("answer")
    if ans == "na":
        return None
    if crit["type"] == "scale":
        return int(item.get("score") or 0)
    return ans == "yes"


def evidence_for(crit: dict, item: dict, transcript: list[dict]) -> list[int]:
    """Номера подтверждающих реплик. Если явных нет — проверенный участок (для правила грубых ошибок —
    только реплики оператора в нём), для N/A — первая содержательная реплика клиента."""
    n = len(transcript)
    ev = sorted({int(e) for e in (item.get("evidence") or []) if 1 <= int(e) <= n})
    if ev:
        return ev
    rng = item.get("checked_range")
    if rng and 1 <= rng[0] <= rng[1] <= n:
        turns = list(range(rng[0], rng[1] + 1))
        if crit["type"] == "flag":
            turns = [i for i in turns if transcript[i - 1].get("speaker") == "operator"] or turns
        return turns[:10]
    req = next((i for i, m in enumerate(transcript, start=1)
                if m.get("speaker") == "client" and len((m.get("text") or "").split()) >= 3), None)
    if req:
        return [req]
    op = next((i for i, m in enumerate(transcript, start=1) if m.get("speaker") == "operator"), None)
    return [op or 1]


def rationale_for(item: dict) -> str:
    text = (item.get("explanation") or "").strip()
    na = (item.get("na_reason") or "").strip()
    if item.get("answer") == "na" and na and na not in text:
        text = (text + " " if text else "") + f"N/A: {na}"
    return text or "Без пояснения."


def model_info() -> dict:
    mode = os.environ.get("CQA_EXPORT_MODEL_MODE", "local")
    name = os.environ.get("CQA_EXPORT_MODEL_NAME", "")
    return {"mode": mode, "name": name}


def build_results(group_id: str, *, team_name: str, source: str = "original",
                  final_for=None, appeals_for=None, get_checklist=None) -> dict[str, Any]:
    """Собирает результаты по всем диалогам набора (последняя успешная оценка каждого диалога)."""
    results = []
    used_models: set[str] = set()
    # Сначала читаем всё из базы одной транзакцией, потом обрабатываем вне её
    # (get_checklist сам берёт блокировку базы — вложенный вызов привёл бы к взаимной блокировке).
    loaded = []
    with tx() as con:
        dialogs = [row(r, ("transcript",)) for r in con.execute(
            "SELECT conversation_id, transcript FROM dialogs WHERE group_id=? ORDER BY rowid", (group_id,))]
        for d in dialogs:
            cid = d["conversation_id"]
            ev = row(con.execute(
                "SELECT * FROM evaluations WHERE group_id=? AND conversation_id=? AND status='done' "
                "ORDER BY id DESC LIMIT 1", (group_id, cid)).fetchone(), ("items", "score", "usage"))
            last_err = None if ev else con.execute(
                "SELECT error FROM evaluations WHERE group_id=? AND conversation_id=? ORDER BY id DESC LIMIT 1",
                (group_id, cid)).fetchone()
            aps = appeals_for(con, ev["id"]) if (ev and source == "final") else []
            loaded.append((d, ev, last_err, aps))
    for d, ev, last_err, aps in loaded:
        cid = d["conversation_id"]
        if not ev:
            results.append({"conversation_id": cid, "status": "error", "criteria": [], "score": None,
                            "error": (last_err["error"] if last_err and last_err["error"] else "Диалог не оценён.")})
            continue
        cl = get_checklist(ev["checklist_id"])
        items, score = ev["items"], ev["score"]
        if source == "final":
            fin = final_for(ev, cl, aps)
            items, score = fin["items"], fin["score"]
        by_id = {c["id"]: c for c in cl["data"]["criteria"]}
        missing = [code for cid_, code in EXPORT_CODES.items() if cid_ not in by_id or cid_ not in items]
        if missing or score.get("total") is None:
            why = (f"В чек-листе #{cl['id']} нет критериев для кодов: {', '.join(missing)}." if missing
                   else "Итог не рассчитан: нет применимых критериев с весом.")
            results.append({"conversation_id": cid, "status": "error", "criteria": [], "score": None, "error": why})
            continue
        criteria = []
        for our_id, code in EXPORT_CODES.items():
            crit, item = by_id[our_id], items[our_id]
            criteria.append({"criterion_id": code, "value": to_value(crit, item),
                             "rationale": rationale_for(item),
                             "evidence_turns": evidence_for(crit, item, d["transcript"])})
        critical = next(c["value"] for c in criteria if c["criterion_id"] == "critical")
        results.append({"conversation_id": cid, "status": "ok", "criteria": criteria,
                        "score": {"final_score": score["total"], "critical_flag": bool(critical)}})
        used_models.add(ev.get("evaluator") or "")

    model = model_info()
    if not model["name"]:
        names = sorted(m.replace("ollama/", "") for m in used_models if m)
        rules = any(m.startswith("ollama/") for m in used_models)
        model["name"] = ", ".join(names) + (" + rule engine (rules.py)" if rules else "") if names else "unknown"
    return {"schema_version": SCHEMA_VERSION, "team_name": team_name, "model": model, "results": results}


def validate(doc: dict, expected_ids: list[str] | None = None, expected_count: int | None = None) -> list[str]:
    """Проверка по правилам заполнения из приложения к гайду. Пустой список — всё в порядке."""
    errs = []
    for k in ("schema_version", "team_name", "model", "results"):
        if k not in doc:
            errs.append(f"нет поля {k}")
    if errs:
        return errs
    if doc["schema_version"] != SCHEMA_VERSION:
        errs.append("schema_version должен быть «1.0»")
    if not str(doc["team_name"]).strip():
        errs.append("пустой team_name")
    if doc["model"].get("mode") not in ("local", "external_api", "hybrid") or not doc["model"].get("name"):
        errs.append("model: mode — local/external_api/hybrid, name — не пустой")
    ids = [r.get("conversation_id") for r in doc["results"]]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        errs.append(f"повторяющиеся conversation_id: {dup}")
    if expected_ids is not None:
        miss, extra = sorted(set(expected_ids) - set(ids)), sorted(set(ids) - set(expected_ids))
        if miss:
            errs.append(f"нет записей для: {miss}")
        if extra:
            errs.append(f"лишние записи: {extra}")
    if expected_count is not None and len(ids) != expected_count:
        errs.append(f"ожидалось {expected_count} записей, есть {len(ids)}")
    for r in doc["results"]:
        cid = r.get("conversation_id")
        if r.get("status") == "error":
            if r.get("criteria") != [] or r.get("score") is not None or not r.get("error"):
                errs.append(f"{cid}: для status=error нужны пустые criteria, score=null и причина в error")
            continue
        if r.get("status") != "ok":
            errs.append(f"{cid}: status должен быть ok или error")
            continue
        codes = [c.get("criterion_id") for c in r.get("criteria", [])]
        if sorted(codes) != sorted(CODE_ORDER):
            errs.append(f"{cid}: нужны все 8 критериев ровно по одному разу, есть {codes}")
            continue
        for c in r["criteria"]:
            v, code = c.get("value"), c["criterion_id"]
            ok = (v in ALLOWED[code]) and not (isinstance(v, bool) and code in ("consultation", "dialogue")) \
                and not (isinstance(v, int) and not isinstance(v, bool) and code not in ("consultation", "dialogue"))
            if not ok:
                errs.append(f"{cid}/{code}: недопустимое значение {v!r}")
            if not isinstance(c.get("rationale"), str) or not c["rationale"].strip():
                errs.append(f"{cid}/{code}: пустой rationale")
            et = c.get("evidence_turns")
            if not isinstance(et, list) or not all(isinstance(x, int) and not isinstance(x, bool) and x >= 1 for x in et):
                errs.append(f"{cid}/{code}: evidence_turns — список номеров реплик с 1")
        s = r.get("score") or {}
        fs = s.get("final_score")
        if not isinstance(fs, (int, float)) or isinstance(fs, bool) or not 0 <= fs <= 100 or round(fs, 2) != fs:
            errs.append(f"{cid}: final_score — число 0–100 с двумя знаками, есть {fs!r}")
        crit_v = next(c["value"] for c in r["criteria"] if c["criterion_id"] == "critical")
        if s.get("critical_flag") is not crit_v:
            errs.append(f"{cid}: critical_flag должен совпадать с value критерия critical")
    return errs


# Веса и максимумы шкал из таблицы «Правила заполнения» — для самопроверки final_score
FORMAT_WEIGHTS = {"contact": 10, "discovery": 15, "identification": 20, "consultation": 20,
                  "empathy": 15, "dialogue": 15, "closure": 5}
FORMAT_SCALE_MAX = {"consultation": 20, "dialogue": 10}


def recompute(criteria: list[dict]) -> float | None:
    earned = applicable = 0.0
    for c in criteria:
        code, v = c["criterion_id"], c["value"]
        if code not in FORMAT_WEIGHTS or v is None:
            continue
        w = FORMAT_WEIGHTS[code]
        applicable += w
        earned += w * (v / FORMAT_SCALE_MAX[code] if code in FORMAT_SCALE_MAX else (1 if v else 0))
    if not applicable:
        return None
    from scoring import round2
    return round2(earned * 100 / applicable)


def warnings(doc: dict) -> list[str]:
    """Не ошибки формата, но стоит проверить: итог не совпадает с пересчётом по весам формата
    (например, если чек-лист изменён), диалоги со status=error."""
    out = []
    for r in doc.get("results", []):
        if r.get("status") == "error":
            out.append(f"{r['conversation_id']}: status=error — {r.get('error')}")
            continue
        rc = recompute(r["criteria"])
        if rc is not None and abs(rc - r["score"]["final_score"]) > 0.011:
            out.append(f"{r['conversation_id']}: final_score {r['score']['final_score']} ≠ пересчёт по весам формата {rc}")
    return out


def dumps(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2)
