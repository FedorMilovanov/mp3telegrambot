"""Regression tests for v3 patch 67 — quota must not use 503 same-key retry."""

from pathlib import Path


def test_audio_quota_has_priority_over_overload_retry():
    src = Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    assert "_is_quota = is_quota_error(e)" in src
    assert "_is_overload = is_overload_error(e) and not _is_quota" in src
    assert "if _is_quota:" in src
    assert "Quota is project/model-level; retrying same key only wastes time." in src


def test_audio_second_503_circle_is_not_used_for_quota():
    src = Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    assert "(not is_quota_error(last_err)) and is_overload_error(last_err)" in src
