import sqlite3
from pathlib import Path

from core import observability
from core.candidate_schema import shorts_response_schema
from core.globals import make_audio_config, make_text_config_smart
from core.observability import format_gemini_metrics_report, log_gemini_run


def _dump_config(config) -> str:
    return str(config).lower()


def test_structured_output_configs_allow_thinking_config_with_schema_for_3x():
    schema = shorts_response_schema()
    audio_cfg = make_audio_config(
        model_name="gemini-3.5-flash",
        thinking_level="low",
        response_mime_type="application/json",
        response_schema=schema,
    )
    text_cfg = make_text_config_smart(
        model_name="gemini-3.5-flash",
        thinking_level="low",
        response_mime_type="application/json",
        response_schema=schema,
    )
    assert "thinking_config=" in _dump_config(audio_cfg)
    assert "thinkinglevel.low" in _dump_config(audio_cfg) or "thinking_level" in _dump_config(audio_cfg)
    assert "thinkinglevel.low" in _dump_config(text_cfg) or "thinking_level" in _dump_config(text_cfg)
    assert "response_mime_type='application/json'" in _dump_config(audio_cfg)


def test_observability_table_ensure_is_cached_per_db_path(tmp_path, monkeypatch):
    db1 = tmp_path / "one.db"
    db2 = tmp_path / "two.db"
    monkeypatch.setattr(observability, "DB_PATH", db1)
    first = log_gemini_run(task="audio_analysis")
    second = log_gemini_run(task="audio_analysis")
    assert isinstance(first, int)
    assert isinstance(second, int)
    assert str(db1) in observability._TABLE_ENSURED_DB_PATHS

    monkeypatch.setattr(observability, "DB_PATH", db2)
    third = log_gemini_run(task="shorts_candidates")
    assert isinstance(third, int)
    with sqlite3.connect(db2) as conn:
        count = conn.execute("SELECT COUNT(*) FROM gemini_runs").fetchone()[0]
    assert count == 1


def test_metrics_report_truncation_has_visible_notice(tmp_path, monkeypatch):
    db_path = tmp_path / "long_metrics.db"
    monkeypatch.setattr(observability, "DB_PATH", db_path)
    for i in range(80):
        log_gemini_run(
            task=f"task_{i}",
            model="gemini-3.5-flash",
            error="x" * 120,
            json_valid=False,
            total_tokens=100,
        )
    report = format_gemini_metrics_report(hours=24, recent_limit=50)
    assert len(report) <= 3900
    assert "Отчёт обрезан" in report


def test_ci_runs_supported_python_matrix():
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert 'python-version: ["3.11", "3.13"]' in ci
    assert "matrix.python-version" in ci
