"""Детерминированный расчёт баллов по правилам чек-листа.

Порядок расчёта (стр. 2 чек-листа):
1. N/A допускается только там, где он разрешён.
2. «Да» даёт полный вес, «Нет» — ноль; шкала — выбранная ступень.
3. Шкала пересчитывается пропорционально весу (верхняя ступень = полный вес).
4. Критерии с N/A исключаются, их вес перераспределяется — максимум остаётся 100.
5. Итог округляется до двух знаков; если применимых критериев нет — итог не считается.
   Грубые ошибки (тип flag) отмечаются отдельно и в баллы не входят.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

YES, NO, NA, SCORE = "yes", "no", "na", "score"


def round2(x: float | Decimal) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def scale_max(criterion: dict) -> float:
    vals = [s["value"] for s in criterion.get("scale", [])]
    return max(vals) if vals else 0


def raw_points(criterion: dict, item: dict) -> float | None:
    """Баллы критерия до перераспределения (из его веса). None — критерий не даёт баллов."""
    ctype = criterion["type"]
    weight = float(criterion.get("weight", 0) or 0)
    answer = item.get("answer")
    if ctype == "flag" or weight <= 0 or answer == NA:
        return None
    if ctype == "binary":
        return weight if answer == YES else 0.0
    if ctype == "scale":
        mx = scale_max(criterion)
        if mx <= 0:
            return 0.0
        return weight * float(item.get("score") or 0) / mx
    return None


def compute(checklist: dict, items: dict[str, dict]) -> dict[str, Any]:
    """items: {criterion_id: {"answer": yes|no|na|score, "score": int}}.

    Возвращает итог, баллы по критериям (до и после перераспределения),
    результаты секций и флаг грубой ошибки.
    """
    per: dict[str, dict] = {}
    applicable_weight = 0.0
    earned = 0.0
    gross = False
    gross_ids: list[str] = []

    for c in checklist["criteria"]:
        it = items.get(c["id"], {})
        if c["type"] == "flag":
            hit = it.get("answer") == YES
            gross = gross or hit
            if hit:
                gross_ids.append(c["id"])
            per[c["id"]] = {"raw": None, "max": 0, "final": None, "applicable": False}
            continue
        pts = raw_points(c, it)
        w = float(c.get("weight", 0) or 0)
        if pts is None:
            per[c["id"]] = {"raw": None, "max": w, "final": None, "applicable": False}
            continue
        applicable_weight += w
        earned += pts
        per[c["id"]] = {"raw": pts, "max": w, "final": None, "applicable": True}

    total = None
    if applicable_weight > 0:
        k = 100.0 / applicable_weight
        total = round2(earned * k)
        for cid, p in per.items():
            if p["applicable"]:
                p["final"] = round2(p["raw"] * k)
                p["final_max"] = round2(p["max"] * k)
                p["raw"] = round2(p["raw"])

    sections = []
    for s in checklist.get("sections", []):
        s_earned = s_w = 0.0
        for c in checklist["criteria"]:
            if c.get("section") != s["id"]:
                continue
            p = per.get(c["id"])
            if p and p["applicable"]:
                s_earned += p["raw"]
                s_w += p["max"]
        has_scored = any(c.get("section") == s["id"] and c["type"] != "flag" for c in checklist["criteria"])
        if not has_scored:
            continue
        sections.append({
            "id": s["id"], "title": s["title"],
            "score": round2(s_earned / s_w * 100) if s_w > 0 else None,
            "earned": round2(s_earned), "max": round2(s_w),
        })

    return {
        "total": total,
        "applicable_weight": round2(applicable_weight),
        "gross_error": gross,
        "gross_criteria": gross_ids,
        "criteria": per,
        "sections": sections,
    }


def normalize_item(criterion: dict, item: dict) -> tuple[dict, list[str]]:
    """Приводит ответ модели/проверяющего к допустимому виду. Возвращает (item, warnings)."""
    warnings = []
    ctype = criterion["type"]
    ans = (item.get("answer") or "").lower()
    out = dict(item)
    if ans == NA and not criterion.get("na_allowed"):
        warnings.append("N/A не разрешён для этого критерия — ответ заменён на «Нет»/0, требуется проверка")
        ans = NO if ctype != "scale" else SCORE
        out["score"] = 0
    if ctype in ("binary", "flag"):
        if ans not in (YES, NO, NA):
            warnings.append(f"Недопустимый ответ «{ans}» — заменён на «Нет»")
            ans = NO
    elif ctype == "scale":
        if ans in (YES, NO):
            out["score"] = scale_max(criterion) if ans == YES else 0
            ans = SCORE
        if ans != NA:
            ans = SCORE
            steps = sorted(s["value"] for s in criterion.get("scale", []))
            v = out.get("score")
            v = 0 if v is None else v
            if steps and v not in steps:
                snapped = min(steps, key=lambda s: abs(s - v))
                warnings.append(f"Балл {v} не является ступенью шкалы — округлён до {snapped}")
                v = snapped
            out["score"] = v
    out["answer"] = ans
    if ans != SCORE:
        out["score"] = None
    return out, warnings
