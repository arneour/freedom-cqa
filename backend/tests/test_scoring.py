import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from scoring import compute, normalize_item  # noqa: E402

CL = json.loads((pathlib.Path(__file__).resolve().parents[1] / "default_checklist.json").read_text())
BY_ID = {c["id"]: c for c in CL["criteria"]}


def items(**kw):
    base = {
        "c01": {"answer": "yes"}, "c02": {"answer": "yes"}, "c03": {"answer": "yes"},
        "c04": {"answer": "score", "score": 20}, "c05": {"answer": "yes"},
        "c06": {"answer": "score", "score": 10}, "c07": {"answer": "yes"},
        "c08": {"answer": "no"},
    }
    base.update(kw)
    return base


def test_perfect_is_100():
    r = compute(CL, items())
    assert r["total"] == 100.0
    assert r["gross_error"] is False


def test_na_redistributes_weight():
    # c03 (20) N/A -> max 80; all others full -> 100
    r = compute(CL, items(c03={"answer": "na"}))
    assert r["total"] == 100.0
    assert r["applicable_weight"] == 80.0
    # c01 weight 10 -> 12.5 after redistribution
    assert r["criteria"]["c01"]["final"] == 12.5


def test_scale_proportional():
    # c04 15/20 of weight 20 -> 15 pts; c06 7/10 of 15 -> 10.5; total 100-5-4.5 = 90.5
    r = compute(CL, items(c04={"answer": "score", "score": 15}, c06={"answer": "score", "score": 7}))
    assert r["total"] == 90.5


def test_na_and_no_mixed_rounding():
    # c02 N/A, c07 N/A -> applicable 80; c01 no -> 70/80 = 87.5
    r = compute(CL, items(c02={"answer": "na"}, c07={"answer": "na"}, c01={"answer": "no"}))
    assert r["total"] == 87.5
    # c03 N/A -> 60 applicable... c06 4/10 -> 6 of 15; earned 10+15+20+15+6=66? check rounding to 2 dp
    r = compute(CL, items(c03={"answer": "na"}, c02={"answer": "na"}, c06={"answer": "score", "score": 4}))
    # applicable = 10+20+15+15+5 = 65, earned = 10+20+15+6+5 = 56 -> 86.153.. -> 86.15
    assert r["total"] == 86.15


def test_gross_error_separate():
    r = compute(CL, items(c08={"answer": "yes"}))
    assert r["gross_error"] is True
    assert r["total"] == 100.0


def test_no_applicable():
    cl = {"criteria": [{"id": "x", "type": "binary", "weight": 10, "na_allowed": True}], "sections": []}
    r = compute(cl, {"x": {"answer": "na"}})
    assert r["total"] is None


def test_normalize_na_not_allowed():
    it, w = normalize_item(BY_ID["c01"], {"answer": "na"})
    assert it["answer"] == "no" and w


def test_normalize_snaps_scale():
    it, w = normalize_item(BY_ID["c06"], {"answer": "score", "score": 8})
    assert it["score"] == 7 and w


def test_sections():
    r = compute(CL, items(c01={"answer": "no"}))
    s1 = next(s for s in r["sections"] if s["id"] == "s1")
    assert s1["score"] == 60.0  # 15/25
