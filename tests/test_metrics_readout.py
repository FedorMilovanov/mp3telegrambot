import sqlite3
from pathlib import Path

from core import observability
from core.observability import format_gemini_metrics_report, log_gemini_run, summarize_gemini_runs


def test_metrics_summary_and_report_include_task_tokens_and_errors(tmp_path, monkeypatch):
    db_path = tmp_path / "metrics.db"
    monkeypatch.setattr(observability, "DB_PATH", db_path)

    log_gemini_run(
        task="audio_analysis",
        video_id="v1",
        model="gemini-3.5-flash",
        thinking_level="high",
        input_tokens=10,
        output_tokens=20,
        thinking_tokens=5,
        total_tokens=35,
        duration_ms=1000,
        json_valid=True,
        finish_reason="STOP",
    )
    log_gemini_run(
        task="shorts_candidates",
        video_id="v1",
        model="gemini-3.5-flash",
        thinking_level="low",
        duration_ms=500,
        json_valid=False,
        postprocess_fixes=3,
        validation_summary={
            "accepted": 2,
            "rejected": 3,
            "total": 5,
            "reasons": {"too_short": 2, "end_after_duration": 1},
        },
        error="json_decode_error",
        finish_reason="MAX_TOKENS",
    )

    summary = summarize_gemini_runs(hours=24)
    assert summary["total_runs"] == 2
    assert summary["errors"] == 1
    assert summary["json_invalid"] == 1
    assert summary["total_tokens"] == 35
    assert summary["candidate_rejections"] == 3
    assert summary["candidate_rejection_reasons"][0] == {"reason": "too_short", "count": 2}
    assert {row["task"] for row in summary["by_task"]} == {"audio_analysis", "shorts_candidates"}

    report = format_gemini_metrics_report(hours=24, recent_limit=5)
    assert "Gemini metrics" in report
    assert "audio_analysis" in report
    assert "shorts_candidates" in report
    assert "json_decode_error" in report
    assert "MAX_TOKENS" in report
    assert "Candidate rejection reasons" in report
    assert "too_short" in report

    with sqlite3.connect(db_path) as conn:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(gemini_runs)").fetchall()}
    assert "idx_gemini_runs_task_ts" in indexes


def test_metrics_report_escapes_html_from_logged_fields(tmp_path, monkeypatch):
    db_path = tmp_path / "metrics_html.db"
    monkeypatch.setattr(observability, "DB_PATH", db_path)

    log_gemini_run(
        task="task<bad>&x",
        model="model<bad>&x",
        error="error <b>boom</b> & details",
        finish_reason="STOP<bad>&x",
        validation_summary={"reasons": {"reason<bad>&x": 1}, "rejected": 1},
        json_valid=False,
    )

    report = format_gemini_metrics_report(hours=24, recent_limit=5)

    assert "task<bad>&x" not in report
    assert "model<bad>&x" not in report
    assert "error <b>boom</b> & details" not in report
    assert "STOP<bad>&x" not in report
    assert "reason<bad>&x" not in report
    assert "task&lt;bad&gt;&amp;x" in report
    assert "model&lt;bad&gt;&amp;x" in report
    assert "error &lt;b&gt;boom&lt;/b&gt; &amp; details" in report
    assert "STOP&lt;bad&gt;&amp;x" in report
    assert "reason&lt;bad&gt;&amp;x" in report


def test_metrics_command_is_registered_in_main_and_admin_menu():
    main_source = Path("main.py").read_text(encoding="utf-8")
    command_source = Path("handlers/commands.py").read_text(encoding="utf-8")
    assert "metrics_command" in main_source
    assert 'CommandHandler("metrics"' in main_source
    assert 'BotCommand("metrics"' in main_source
    assert "def metrics_command" in command_source
    assert "format_gemini_metrics_report" in command_source
