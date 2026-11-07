import collections
import json
import pathlib
import sys
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from demo_operators import item_pct  # noqa: E402

DEMO = json.loads((ROOT / "samples" / "demo_operators.json").read_text(encoding="utf-8"))
RECS = {r["conversation_id"]: r for r in json.loads((ROOT / "samples" / "DEV" / "DEV_all.json").read_text(encoding="utf-8"))["data"]["records"]}


def test_item_pct():
    scale = {"type": "scale", "scale": [{"value": 20}, {"value": 15}, {"value": 0}]}
    assert item_pct(scale, {"answer": "score", "score": 15}) == 75.0
    assert item_pct({"type": "binary"}, {"answer": "yes"}) == 100.0
    assert item_pct({"type": "binary"}, {"answer": "no"}) == 0.0
    assert item_pct({"type": "binary"}, {"answer": "na"}) is None


def test_all_dialogs_assigned_evenly_and_mixed():
    assert len(DEMO["operators"]) == 5
    assert {d["conversation_id"] for d in DEMO["dialogs"]} == set(RECS)
    per = collections.defaultdict(collections.Counter)
    for d in DEMO["dialogs"]:
        r = RECS[d["conversation_id"]]
        per[d["operator_id"]][(r["channel"], r["language"])] += 1
    for op in DEMO["operators"]:
        c = per[op["id"]]
        assert sum(c.values()) == 13
        assert c[("agent chat", "RU")] == 6 and c[("agent chat", "KZ")] == 3 and c[("agent chat", "EN")] == 3
        assert c[("voice call", "RU")] == 1


def test_dates_in_period_and_calls_keep_real_dates():
    lo = datetime.fromisoformat(DEMO["period"]["from"] + "T00:00:00+05:00")
    hi = datetime.fromisoformat(DEMO["period"]["to"] + "T23:59:59+05:00")
    for d in DEMO["dialogs"]:
        start, end = datetime.fromisoformat(d["started_at"]), datetime.fromisoformat(d["ended_at"])
        assert lo <= start <= hi and end >= start
        if RECS[d["conversation_id"]]["channel"] == "voice call":
            assert d["date_source"] == "transcript"
            real = datetime.fromisoformat(RECS[d["conversation_id"]]["transcript"][0]["timestamp"][:26] + "+00:00")
            assert abs((start - real).total_seconds()) < 1
        else:
            assert d["date_source"] == "generated"
            assert 9 <= start.hour < 20
