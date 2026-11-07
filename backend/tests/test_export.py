import copy
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from export_results import evidence_for, recompute, to_value, validate, warnings  # noqa: E402

# Пример из приложения к гайду (стр. 2): итог 85.38
EXAMPLE = {
    "schema_version": "1.0",
    "team_name": "Команда",
    "model": {"mode": "local", "name": "qwen2.5:3b"},
    "results": [{
        "conversation_id": "demo-001", "status": "ok",
        "criteria": [
            {"criterion_id": "contact", "value": True, "rationale": "x", "evidence_turns": [4]},
            {"criterion_id": "discovery", "value": None, "rationale": "x", "evidence_turns": [1]},
            {"criterion_id": "identification", "value": None, "rationale": "x", "evidence_turns": [1]},
            {"criterion_id": "consultation", "value": 15, "rationale": "x", "evidence_turns": [5, 6]},
            {"criterion_id": "empathy", "value": True, "rationale": "x", "evidence_turns": [4, 5]},
            {"criterion_id": "dialogue", "value": 7, "rationale": "x", "evidence_turns": [5, 6]},
            {"criterion_id": "closure", "value": True, "rationale": "x", "evidence_turns": [8]},
            {"criterion_id": "critical", "value": False, "rationale": "x", "evidence_turns": [4, 5, 6, 8]},
        ],
        "score": {"final_score": 85.38, "critical_flag": False},
    }],
}


def test_official_example_score_and_valid():
    assert recompute(EXAMPLE["results"][0]["criteria"]) == 85.38
    assert validate(EXAMPLE, expected_ids=["demo-001"]) == []
    assert warnings(EXAMPLE) == []


def test_error_record_rules():
    doc = copy.deepcopy(EXAMPLE)
    doc["results"].append({"conversation_id": "demo-002", "status": "error", "criteria": [], "score": None,
                           "error": "Не удалось получить ответ модели."})
    assert validate(doc, expected_ids=["demo-001", "demo-002"], expected_count=2) == []
    doc["results"][1]["score"] = {"final_score": 0, "critical_flag": False}  # ошибку нельзя подменять нулём
    assert validate(doc)


def test_detects_format_problems():
    doc = copy.deepcopy(EXAMPLE)
    c = doc["results"][0]["criteria"]
    c[5]["value"] = 5            # dialogue: допустимо только 0/4/7/10
    c[0]["value"] = None         # contact: null недопустим
    c[3]["value"] = True         # consultation: число, а не bool
    doc["results"][0]["score"]["critical_flag"] = True  # не совпадает с critical
    errs = " | ".join(validate(doc))
    assert "dialogue" in errs and "contact" in errs and "consultation" in errs and "critical_flag" in errs


def test_missing_and_duplicate_ids_and_count():
    doc = copy.deepcopy(EXAMPLE)
    doc["results"].append(copy.deepcopy(doc["results"][0]))
    errs = " | ".join(validate(doc, expected_ids=["demo-001", "demo-003"], expected_count=10))
    assert "повторяющиеся" in errs and "demo-003" in errs and "ожидалось 10" in errs


def test_missing_criterion():
    doc = copy.deepcopy(EXAMPLE)
    doc["results"][0]["criteria"].pop()
    assert any("все 8 критериев" in e for e in validate(doc))


def test_value_conversion():
    assert to_value({"type": "binary"}, {"answer": "yes"}) is True
    assert to_value({"type": "binary"}, {"answer": "no"}) is False
    assert to_value({"type": "binary"}, {"answer": "na"}) is None
    assert to_value({"type": "scale"}, {"answer": "score", "score": 15}) == 15
    assert to_value({"type": "scale"}, {"answer": "na"}) is None
    assert to_value({"type": "flag"}, {"answer": "yes"}) is True


def test_evidence_fallbacks():
    tr = [{"speaker": "client", "text": "Где посмотреть мой тариф?"}, {"speaker": "bot", "text": "..."},
          {"speaker": "operator", "text": "Здравствуйте"}, {"speaker": "client", "text": "ок"},
          {"speaker": "operator", "text": "До свидания"}]
    assert evidence_for({"type": "binary"}, {"evidence": [3, 99]}, tr) == [3]          # вне диапазона отброшено
    assert evidence_for({"type": "flag"}, {"evidence": [], "checked_range": [3, 5]}, tr) == [3, 5]  # только оператор
    assert evidence_for({"type": "binary"}, {"evidence": [], "checked_range": None}, tr) == [1]     # запрос клиента
