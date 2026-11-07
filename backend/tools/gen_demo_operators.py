"""Генерация ДЕМО-данных для страницы «Операторы»: 5 операторов, назначение диалогов и даты.

Исходные наборы диалогов не меняются — читаются только для списка ID, канала, языка и длительности.
Результат пишется в отдельный файл samples/demo_operators.json.

Запуск:  .venv/bin/python backend/tools/gen_demo_operators.py
Распределение детерминированное (фиксированный seed): повторный запуск даёт тот же файл.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "samples" / "DEV" / "DEV_all.json"
OUT = ROOT / "samples" / "demo_operators.json"

ALMATY = timezone(timedelta(hours=5))
PERIOD_START = datetime(2026, 10, 10, tzinfo=ALMATY)
PERIOD_DAYS = 28  # 10 окт – 6 ноя 2026
SEED = 20261107

OPERATORS = [
    {"id": "op-1", "name": "Алия Сарсенова", "team": "Поддержка брокера"},
    {"id": "op-2", "name": "Ерлан Жумабаев", "team": "Поддержка брокера"},
    {"id": "op-3", "name": "Камила Нурланова", "team": "Поддержка брокера"},
    {"id": "op-4", "name": "Данияр Оспанов", "team": "Поддержка брокера"},
    {"id": "op-5", "name": "Мария Ковалёва", "team": "Поддержка брокера"},
]


def mmss_to_sec(ts: str) -> int:
    parts = [int(p) for p in ts.split(":")]
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return sec


def main():
    records = json.loads(SRC.read_text(encoding="utf-8"))["data"]["records"]
    rnd = random.Random(SEED)

    # Поровну и вперемешку: каждой группе (канал+язык) — по кругу между операторами.
    groups: dict[tuple, list] = {}
    for r in records:
        groups.setdefault((r["channel"], r["language"]), []).append(r)
    assign: dict[str, str] = {}
    counts = {o["id"]: 0 for o in OPERATORS}
    for key in sorted(groups):
        items = sorted(groups[key], key=lambda r: r["conversation_id"])
        rnd.shuffle(items)
        for r in items:
            # следующий — тот, у кого меньше всего диалогов (при равенстве — по порядку)
            op = min(OPERATORS, key=lambda o: (counts[o["id"]], o["id"]))["id"]
            assign[r["conversation_id"]] = op
            counts[op] += 1

    dialogs = []
    for r in sorted(records, key=lambda r: r["conversation_id"]):
        tr = r["transcript"]
        if r["channel"] == "voice call":
            # у звонков настоящие даты — сохраняем их (в часовом поясе Алматы)
            start = datetime.fromisoformat(tr[0]["timestamp"][:26] + "+00:00").astimezone(ALMATY)
            end = datetime.fromisoformat(tr[-1]["timestamp"][:26] + "+00:00").astimezone(ALMATY)
            source = "transcript"
        else:
            day = rnd.randrange(PERIOD_DAYS)
            minute = rnd.randrange(9 * 60, 20 * 60)  # рабочее время 09:00–20:00
            start = PERIOD_START + timedelta(days=day, minutes=minute, seconds=rnd.randrange(60))
            end = start + timedelta(seconds=mmss_to_sec(tr[-1]["timestamp"]))
            source = "generated"
        dialogs.append({
            "conversation_id": r["conversation_id"],
            "group_id": "DEV",
            "operator_id": assign[r["conversation_id"]],
            "started_at": start.isoformat(timespec="seconds"),
            "ended_at": end.isoformat(timespec="seconds"),
            "date_source": source,
        })

    out = {
        "_note": "ДЕМО-данные: операторы, назначение диалогов и даты сгенерированы для показа профилей. "
                 "В исходном наборе DEV этих полей нет.",
        "generated_by": "backend/tools/gen_demo_operators.py",
        "seed": SEED,
        "period": {"from": PERIOD_START.date().isoformat(),
                   "to": (PERIOD_START + timedelta(days=PERIOD_DAYS - 1)).date().isoformat(),
                   "timezone": "Asia/Almaty (+05:00)"},
        "operators": OPERATORS,
        "dialogs": dialogs,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {len(dialogs)} диалогов, по операторам: {counts}")


if __name__ == "__main__":
    main()
