import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).parents[1] / "services/livedub_qa_hardening.py"
    spec = importlib.util.spec_from_file_location("livedub_qa_focused_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


qa = _load_module()


def test_focused_window_replaces_broad_diagnosis_and_correction():
    primary = {
        "issues": [{
            "time": "18:32",
            "heard": "правый глаз соблазняет тебя",
            "problem": "широкий проход предположил одну проблему",
            "should_be": "старая предлагаемая формулировка",
            "severity": "major",
        }]
    }
    focused = {
        "issues": [{
            "time": "18:34",
            "heard": "если правый глаз соблазняет тебя",
            "problem": "точечный проход установил реальную проблему",
            "should_be": "проверенная формулировка по оригиналу",
            "severity": "major",
        }]
    }
    result = qa.confirmed_result_one_to_one(primary, focused)
    assert result["issues"][0]["problem"] == focused["issues"][0]["problem"]
    assert result["issues"][0]["should_be"] == focused["issues"][0]["should_be"]
    assert result["issues"][0]["heard"] == focused["issues"][0]["heard"]
