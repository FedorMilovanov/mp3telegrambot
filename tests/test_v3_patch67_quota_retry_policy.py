"""Regression tests for v3 patch 67 — quota must not use 503 same-key retry."""

from pathlib import Path


def test_audio_quota_has_priority_over_overload_retry():
    src = Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    assert "_is_quota = is_quota_error(e)" in src
    assert "_is_overload = is_overload_error(e) and not _is_quota" in src
    assert "if _is_quota:" in src
    assert "Quota is project/model-level; retrying same key only wastes time." in src


def test_audio_has_no_second_full_503_circle_for_any_error_class():
    src = Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    assert "second full re-upload circle is disabled" in src
    assert "await asyncio.sleep(60)" not in src
    assert "Gemini: второй круг успешен!" not in src
