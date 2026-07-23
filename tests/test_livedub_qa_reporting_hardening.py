import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).parents[1] / "services/livedub_qa_hardening.py"
    spec = importlib.util.spec_from_file_location("livedub_qa_reporting_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


qa = _load_module()


def test_notes_are_inserted_after_report_heading():
    assert qa._insert_after_head("HEAD\nBODY", ["NOTE 1", "NOTE 2"]) == (
        "HEAD\nNOTE 1\nNOTE 2\nBODY"
    )


def test_old_low_confidence_message_is_recognized_exactly():
    assert "Оригинальное аудио было недоступно" in qa._OLD_LOW_CONFIDENCE_NOTE
    assert "по конспекту" in qa._OLD_LOW_CONFIDENCE_NOTE


def test_runtime_audits_separate_original_and_russian_availability():
    src = (Path(__file__).parents[1] / "services/livedub_qa_hardening.py").read_text(
        encoding="utf-8"
    )
    assert 'result["_qa_original_reference_available"]' in src
    assert 'result["_qa_local_original_available"]' in src
    assert 'result["_qa_russian_audio_available"]' in src
    assert 'os.environ.setdefault("LIVEDUB_QA_VERIFY_MAX_ISSUES", "10")' in src


def test_verification_limit_never_publishes_unchecked_candidates():
    src = (Path(__file__).parents[1] / "services/livedub_qa_hardening.py").read_text(
        encoding="utf-8"
    )
    assert 'limited_primary["issues"] = selected' in src
    assert 'result["_qa_verification_limit_dropped"] = omitted' in src
    assert 'result["_qa_unconfirmed_dropped"]' in src
