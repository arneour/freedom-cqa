"""ДЕМО: профили операторов и лидерборд.

Изолировано от основной функции:
- операторы, назначение диалогов и даты берутся из отдельного файла samples/demo_operators.json
  (генерирует backend/tools/gen_demo_operators.py);
- модуль только ЧИТАЕТ оценки и апелляции — таблицы базы не меняются, новых таблиц нет;
- финальные баллы считаются той же функцией, что и в карточке диалога (с учётом апелляций);
- если файла нет или здесь что-то сломается, основное приложение продолжает работать.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Callable

from fastapi import APIRouter, HTTPException

from db import row, tx

DEMO_FILE = Path(__file__).resolve().parent.parent / "samples" / "demo_operators.json"
GROSS_AXIS = {"id": "_no_gross", "code": "", "title": "Без грубых ошибок"}


def load_demo() -> dict:
    if not DEMO_FILE.exists():
        raise HTTPException(404, "Нет файла демо-операторов. Запустите backend/tools/gen_demo_operators.py")
    return json.loads(DEMO_FILE.read_text(encoding="utf-8"))


def item_pct(crit: dict, item: dict) -> float | None:
    """Процент от максимума по критерию: «Да» = 100, «Нет» = 0, шкала — доля от верхней ступени, N/A — нет значения."""
    if not item or item.get("answer") == "na":
        return None
    if crit["type"] == "scale":
        mx = max((s["value"] for s in crit.get("scale", [])), default=0)
        return 100.0 * (item.get("score") or 0) / mx if mx else None
    if crit["type"] == "binary":
        return 100.0 if item.get("answer") == "yes" else 0.0
    return None


def r1(x):
    return None if x is None else round(x, 1)


def make_router(final_for: Callable, appeals_for: Callable, get_checklist: Callable,
                active_checklist: Callable) -> APIRouter:
    router = APIRouter(prefix="/api/demo")

    def collect():
        demo = load_demo()
        ops = {o["id"]: o for o in demo["operators"]}
        cl = active_checklist()
        axes = [c for c in cl["data"]["criteria"] if c["type"] != "flag" and (c.get("weight") or 0) > 0]
        cls_cache: dict[int, dict] = {}
        rows = []
        with tx() as con:
            for d in demo["dialogs"]:
                ev = row(con.execute(
                    "SELECT * FROM evaluations WHERE group_id=? AND conversation_id=? AND status='done' "
                    "ORDER BY id DESC LIMIT 1", (d["group_id"], d["conversation_id"])).fetchone(),
                    ("items", "score", "usage"))
                meta = con.execute("SELECT channel, language FROM dialogs WHERE group_id=? AND conversation_id=?",
                                   (d["group_id"], d["conversation_id"])).fetchone()
                aps = appeals_for(con, ev["id"]) if ev else []
                rows.append({**d, "ev": ev, "appeals": aps,
                             "channel": meta["channel"] if meta else None,
                             "language": meta["language"] if meta else None})
        for r in rows:
            ev = r["ev"]
            r["final"] = None
            if ev:
                if ev["checklist_id"] not in cls_cache:
                    cls_cache[ev["checklist_id"]] = get_checklist(ev["checklist_id"])
                fcl = cls_cache[ev["checklist_id"]]
                r["final"] = final_for(ev, fcl, r["appeals"])
                r["crit_by_id"] = {c["id"]: c for c in fcl["data"]["criteria"]}
            r["date"] = datetime.fromisoformat(r["started_at"])
        return demo, ops, cl, axes, rows

    def axis_values(axes, rows):
        out = []
        for a in axes:
            vals, samples = [], []
            for r in rows:
                if not r["final"] or a["id"] not in r.get("crit_by_id", {}):
                    continue
                p = item_pct(r["crit_by_id"][a["id"]], r["final"]["items"].get(a["id"]))
                if p is not None:
                    vals.append(p)
                    samples.append((p, r["conversation_id"]))
            out.append({"id": a["id"], "code": a.get("code", ""), "title": a["title"],
                        "value": r1(mean(vals)) if vals else None, "n": len(vals), "samples": samples})
        ev_rows = [r for r in rows if r["final"]]
        clean = [0 if r["final"]["score"]["gross_error"] else 100 for r in ev_rows]
        out.append({**GROSS_AXIS, "value": r1(mean(clean)) if clean else None, "n": len(clean), "samples": []})
        return out

    def totals(rows):
        return [r["final"]["score"]["total"] for r in rows if r["final"] and r["final"]["score"]["total"] is not None]

    def summary(op, rows, period_end):
        t = totals(rows)
        mid = period_end - timedelta(days=14)
        last = totals([r for r in rows if r["date"] > mid])
        prev = totals([r for r in rows if r["date"] <= mid])
        aps = [a for r in rows for a in r["appeals"]]
        return {
            "id": op["id"], "name": op["name"], "team": op.get("team", ""),
            "dialogs": len(rows), "evaluated": len(t),
            "avg": r1(mean(t)) if t else None,
            "best": r1(max(t)) if t else None, "worst": r1(min(t)) if t else None,
            "gross": sum(1 for r in rows if r["final"] and r["final"]["score"]["gross_error"]),
            "trend": r1(mean(last) - mean(prev)) if last and prev else None,
            "appeals": len(aps),
        }

    def period_end(demo):
        return datetime.fromisoformat(demo["period"]["to"] + "T23:59:59+05:00")

    @router.get("/leaderboard")
    def leaderboard():
        demo, ops, cl, axes, rows = collect()
        pe = period_end(demo)
        board = [summary(o, [r for r in rows if r["operator_id"] == o["id"]], pe) for o in demo["operators"]]
        # Место — по среднему финальному итогу; грубые ошибки показываются отдельной колонкой.
        board.sort(key=lambda b: (b["avg"] is None, -(b["avg"] or 0)))
        for i, b in enumerate(board, start=1):
            b["rank"] = i
        team = axis_values(axes, rows)
        for b in board:
            b["axes"] = [{k: v for k, v in a.items() if k != "samples"}
                         for a in axis_values(axes, [r for r in rows if r["operator_id"] == b["id"]])]
        return {"note": demo.get("_note"), "period": demo["period"], "checklist": {"id": cl["id"], "name": cl["name"]},
                "team_axes": [{k: v for k, v in a.items() if k != "samples"} for a in team],
                "team_avg": r1(mean(totals(rows))) if totals(rows) else None,
                "operators": board}

    @router.get("/operators/{op_id}")
    def profile(op_id: str):
        demo, ops, cl, axes, rows = collect()
        if op_id not in ops:
            raise HTTPException(404, "Оператор не найден")
        pe = period_end(demo)
        mine = [r for r in rows if r["operator_id"] == op_id]
        board = leaderboard()["operators"]
        me = next(b for b in board if b["id"] == op_id)

        my_axes = axis_values(axes, mine)
        team_axes = axis_values(axes, rows)

        # Сильные и слабые стороны — среди критериев с баллами (без оси грубых ошибок)
        scored = [a for a in my_axes if a["id"] != GROSS_AXIS["id"] and a["value"] is not None]
        ranked = sorted(scored, key=lambda a: a["value"])

        def example(a, best):
            if not a["samples"]:
                return None
            p, cid = (max if best else min)(a["samples"], key=lambda s: s[0])
            return {"conversation_id": cid, "value": r1(p)}

        strengths = [{"id": a["id"], "title": a["title"], "value": a["value"], "n": a["n"], "example": example(a, True)}
                     for a in reversed(ranked[-2:])]
        weaknesses = [{"id": a["id"], "title": a["title"], "value": a["value"], "n": a["n"], "example": example(a, False)}
                      for a in ranked[:2]]

        # Прогрессия по неделям (понедельник — начало недели), плюс среднее по команде
        def week(dt):
            return (dt - timedelta(days=dt.weekday())).date().isoformat()

        weeks = sorted({week(r["date"]) for r in rows})
        progression = []
        for w in weeks:
            mt = totals([r for r in mine if week(r["date"]) == w])
            tt = totals([r for r in rows if week(r["date"]) == w])
            progression.append({"week": w, "avg": r1(mean(mt)) if mt else None, "n": len(mt),
                                "team_avg": r1(mean(tt)) if tt else None})

        aps = [a for r in mine for a in r["appeals"]]
        appeals = {"total": len(aps),
                   "open": sum(a["status"] == "open" for a in aps),
                   "changed": sum(a["status"] == "resolved" and a["decision"] == "changed" for a in aps),
                   "confirmed": sum(a["status"] == "resolved" and a["decision"] == "confirmed" for a in aps)}

        dialogs = []
        for r in sorted(mine, key=lambda r: r["date"], reverse=True):
            f = r["final"]
            dialogs.append({
                "conversation_id": r["conversation_id"], "group_id": r["group_id"],
                "started_at": r["started_at"], "ended_at": r["ended_at"], "date_source": r["date_source"],
                "channel": r["channel"], "language": r["language"],
                "total": f["score"]["total"] if f else None,
                "original_total": r["ev"]["score"]["total"] if r["ev"] else None,
                "gross": bool(f and f["score"]["gross_error"]),
                "appeals": len(r["appeals"]),
            })

        strip = lambda arr: [{k: v for k, v in a.items() if k != "samples"} for a in arr]  # noqa: E731
        return {"note": demo.get("_note"), "period": demo["period"],
                "checklist": {"id": cl["id"], "name": cl["name"]},
                "operator": me, "operators_total": len(board),
                "axes": strip(my_axes), "team_axes": strip(team_axes),
                "strengths": strengths, "weaknesses": weaknesses,
                "progression": progression, "appeals": appeals, "dialogs": dialogs}

    return router
