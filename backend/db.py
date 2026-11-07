"""SQLite-хранилище. Сложные структуры лежат в JSON-колонках."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("CQA_DB", Path(__file__).resolve().parent.parent / "data" / "cqa.sqlite3"))
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS checklists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  data TEXT NOT NULL,
  created_at TEXT NOT NULL,
  parent_id INTEGER,
  is_active INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS dialogs (
  conversation_id TEXT NOT NULL,
  group_id TEXT NOT NULL,
  channel TEXT,
  language TEXT,
  transcript TEXT NOT NULL,
  audio_url TEXT,
  imported_at TEXT NOT NULL,
  PRIMARY KEY (group_id, conversation_id)
);
CREATE TABLE IF NOT EXISTS evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  checklist_id INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,            -- done | error
  evaluator TEXT,                  -- имя модели или 'heuristic'
  items TEXT,                      -- {criterion_id: {...}} исходная оценка
  score TEXT,                      -- результат compute() по исходной оценке
  usage TEXT,                      -- токены и стоимость
  error TEXT
);
CREATE INDEX IF NOT EXISTS ix_eval_dialog ON evaluations(group_id, conversation_id);
CREATE TABLE IF NOT EXISTS appeals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  evaluation_id INTEGER NOT NULL,
  criterion_id TEXT NOT NULL,
  comment TEXT NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',   -- open | resolved
  decision TEXT,                          -- confirmed | changed
  new_item TEXT,                          -- новая оценка критерия (JSON)
  reviewer_reason TEXT,
  resolved_at TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


_con = None


@contextmanager
def tx():
    global _con
    with _lock:
        if _con is None:
            _con = _connect()
            _con.executescript(SCHEMA)
        try:
            yield _con
            _con.commit()
        except Exception:
            _con.rollback()
            raise


def row(r: sqlite3.Row | None, json_cols: tuple[str, ...] = ()) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for c in json_cols:
        if d.get(c) is not None:
            d[c] = json.loads(d[c])
    return d


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False)
