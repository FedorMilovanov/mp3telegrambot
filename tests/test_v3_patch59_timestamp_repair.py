"""Regression tests for v3 patch 59 — targeted audio timestamp coverage repair."""

from pathlib import Path

from core.candidate_schema import timestamp_repair_response_schema
from services.gemini_analyze import _format_repaired_timestamps, _format_ts_for_prompt


def test_timestamp_repair_schema_contract():
    schema = timestamp_repair_response_schema()
    assert schema["required"] == ["timestamps"]
    item = schema["properties"]["timestamps"]["items"]
    assert item["required"] == ["time", "topic"]


def test_format_repaired_timestamps_filters_bad_and_formats():
    data = {"timestamps": [
        {"time": "0:00", "topic": "Начало"},
        {"time": "1:02:03", "topic": "Финал"},
        {"time": "99:00", "topic": "За пределами"},
        {"time": "bad", "topic": "bad"},
    ]}
    out = _format_repaired_timestamps(data, duration=4000)
    assert "0:00 Начало" in out
    assert "1:02:03 Финал" in out
    assert "За пределами" not in out
    assert _format_ts_for_prompt(3300 * 0.88) == "48:24"


def test_gemini_analyze_wires_timestamp_repair_nonfatally():
    src = Path("services/gemini_analyze.py").read_text(encoding="utf-8")
    assert "AUDIO_TIMESTAMP_REPAIR" in src
    assert "timestamp_coverage_warning" in src
    assert "audio_timestamp_repair" in src
    assert "_repair_timestamp_coverage_if_needed" in src
    assert "Timestamp coverage repair failed non-fatally" in src
    assert "response_schema=timestamp_repair_response_schema()" in src
