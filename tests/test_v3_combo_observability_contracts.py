from pathlib import Path


def test_telegraph_text_request_has_observability_contract():
    source = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert "from core.observability import alog_gemini_response, alog_gemini_run" in source
    assert "task: str = \"telegraph_text\"" in source
    assert "task=f\"telegraph_{label.lower()}\"" in source
    assert "task=f\"telegraph_{label.lower()}_retry\"" in source
    assert "json_valid=True" in source
    assert "error=(str(last_err)[:300] if last_err else \"empty_response\")" in source


def test_video_candidate_generators_log_observability_and_use_schema_report():
    source = Path("services/shorts_candidates.py").read_text(encoding="utf-8")
    assert "from core.candidate_schema import CandidateValidationReport, validate_candidate_times" in source
    assert "from core.observability import alog_gemini_response, alog_gemini_run" in source
    assert 'task="shorts_candidates"' in source
    assert 'task="clips_candidates"' in source
    assert "postprocess_fixes=report.rejected" in source
    assert "validate_candidate_times(" in source


def test_extras_candidates_logs_observability():
    source = Path("services/render_clips_montage.py").read_text(encoding="utf-8")
    assert "from core.observability import alog_gemini_response, alog_gemini_run" in source
    assert 'task="extras_candidates"' in source
    assert 'error="json_decode_error"' in source
    assert "json_valid=True" in source
