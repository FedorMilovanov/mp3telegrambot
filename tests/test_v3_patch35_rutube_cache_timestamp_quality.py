"""Regression tests for v3 patch 35 — RuTube cache and timestamp coverage diagnostics."""

from pathlib import Path

from core.json_parser import _parse_gemini_response
from core.timestamp_quality import (
    audit_timestamp_coverage,
    extract_timestamp_seconds,
    timestamp_coverage_ratio,
)
from services.search import _get_rutube_listing_cache, _set_rutube_listing_cache


def test_timestamp_coverage_audit_detects_half_covered_long_material():
    timestamps = "0:00 Начало\n10:00 Середина\n20:00 Всё ещё первая половина"
    issue = audit_timestamp_coverage(timestamps, 60 * 60)
    assert issue is not None
    assert issue.code == "timestamp_coverage_low"
    assert issue.coverage_ratio < 0.72
    assert extract_timestamp_seconds(timestamps) == [0, 600, 1200]
    assert round(timestamp_coverage_ratio(timestamps, 3600), 2) == 0.33


def test_json_parser_marks_timestamp_coverage_warning_without_mutating_timestamps():
    raw = {
        "real_author": "A",
        "real_title": "T",
        "format": "sermon",
        "main_topic": "Тема.",
        "timestamps": [
            {"time": "0:00", "topic": "Начало"},
            {"time": "10:00", "topic": "Середина"},
            {"time": "20:00", "topic": "Первая половина"},
        ],
        "hashtags": ["Тема"],
        "analysis_summary": "Анализ.",
        "argument_arc": "Арка.",
        "key_categories": [],
        "questions": [],
        "terms_data": {"concepts": [], "scripture": [], "translations": [], "lexicon_notes": []},
        "whisper_hints": [],
        "hermeneutic_method": "mixed",
    }
    import json
    parsed = _parse_gemini_response(json.dumps(raw, ensure_ascii=False), duration=3600)
    assert parsed is not None
    assert "0:00 Начало" in parsed["timestamps"]
    assert "timestamp_coverage_warning" in parsed


def test_rutube_listing_cache_roundtrip_is_copy():
    # AUDIT R14: кэш теперь возвращает (results, complete)
    _set_rutube_listing_cache("cid-test", [{"id": "1", "title": "A"}], True)
    first_res, first_complete = _get_rutube_listing_cache("cid-test")
    second_res, _ = _get_rutube_listing_cache("cid-test")
    assert first_res == [{"id": "1", "title": "A"}]
    assert first_complete is True
    assert second_res == [{"id": "1", "title": "A"}]
    assert first_res is not second_res


def test_search_and_json_parser_are_wired_for_cache_and_timestamp_quality():
    search = Path("services/search.py").read_text(encoding="utf-8")
    parser = Path("core/json_parser.py").read_text(encoding="utf-8")
    assert "_RUTUBE_LISTING_CACHE" in search
    assert "_get_rutube_listing_cache" in search
    assert "_set_rutube_listing_cache" in search
    assert "листинг из кэша" in search
    assert "from core.timestamp_quality import audit_timestamp_coverage" in parser
    assert "timestamp_coverage_warning" in parser
