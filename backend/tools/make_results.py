"""Выгрузка results.json для сдачи (формат JSON 1.0 из приложения к гайду).

  .venv/bin/python backend/tools/make_results.py --group DEV  --out results/dev/results.json
  .venv/bin/python backend/tools/make_results.py --group <ID> --out results/prod/results.json --expect 10

Название команды — --team или CQA_TEAM_NAME в .env. Модель — CQA_EXPORT_MODEL_MODE / CQA_EXPORT_MODEL_NAME
(по умолчанию local и фактическая модель из оценок). Выгружается последняя успешная оценка каждого диалога,
по умолчанию исходная оценка системы (--source final — с учётом апелляций).
Файл не записывается, если проверка формата нашла ошибки (кроме --force).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

import app  # noqa: E402  (загружает .env и функции оценки)
import export_results as ex  # noqa: E402
from db import tx  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--group", required=True, help="group_id набора (DEV или выданный для контроля)")
    p.add_argument("--out", required=True, help="куда записать results.json")
    p.add_argument("--team", default=None, help="название команды (иначе CQA_TEAM_NAME)")
    p.add_argument("--source", choices=["original", "final"], default="original")
    p.add_argument("--expect", type=int, default=None, help="ожидаемое число записей (для prod — 10)")
    p.add_argument("--force", action="store_true", help="записать даже при ошибках проверки")
    a = p.parse_args()

    import os
    team = a.team or os.environ.get("CQA_TEAM_NAME", "").strip()
    if not team:
        sys.exit("Укажите название команды: --team \"...\" или CQA_TEAM_NAME в .env")

    with tx() as con:
        ids = [r["conversation_id"] for r in con.execute(
            "SELECT conversation_id FROM dialogs WHERE group_id=? ORDER BY rowid", (a.group,))]
    if not ids:
        sys.exit(f"В базе нет диалогов набора {a.group}. Загрузите его в интерфейсе или через /api/groups/{a.group}/import")

    doc = ex.build_results(a.group, team_name=team, source=a.source, final_for=app.final_for,
                           appeals_for=app.appeals_for, get_checklist=app.get_checklist)
    errs = ex.validate(doc, expected_ids=ids, expected_count=a.expect)
    warns = ex.warnings(doc)
    ok = sum(r["status"] == "ok" for r in doc["results"])
    print(f"Набор {a.group}: записей {len(doc['results'])}, ok {ok}, error {len(doc['results']) - ok}")
    print(f"Команда: {doc['team_name']} · модель: {doc['model']['mode']} / {doc['model']['name']} · оценка: {a.source}")
    for w in warns:
        print("  предупреждение:", w)
    if errs:
        print("ОШИБКИ ФОРМАТА:")
        for e in errs:
            print("  -", e)
        if not a.force:
            sys.exit("Файл не записан. Исправьте ошибки или запустите с --force.")
    out = Path(a.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(ex.dumps(doc) + "\n", encoding="utf-8")
    print(f"Записано: {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")


if __name__ == "__main__":
    main()
