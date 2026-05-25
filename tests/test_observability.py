import asyncio
import sqlite3
from types import SimpleNamespace

from core import observability
from core.observability import (
    alog_gemini_run,
    extract_finish_reason,
    extract_usage_metadata,
    log_gemini_response,
    log_gemini_run,
)


def test_extract_usage_metadata_returns_zeroes_without_metadata():
    assert extract_usage_metadata(object()) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "thinking_tokens": 0,
        "total_tokens": 0,
    }


def test_extract_usage_metadata_reads_gemini_fields():
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=20,
            cached_content_token_count=3,
            thoughts_token_count=7,
            total_token_count=40,
        )
    )
    assert extract_usage_metadata(response) == {
        "input_tokens": 10,
        "output_tokens": 20,
        "cached_tokens": 3,
        "thinking_tokens": 7,
        "total_tokens": 40,
    }


def test_extract_finish_reason_best_effort():
    response = SimpleNamespace(candidates=[SimpleNamespace(finish_reason="STOP")])
    assert extract_finish_reason(response) == "STOP"
    assert extract_finish_reason(SimpleNamespace(candidates=[])) == ""


def test_log_gemini_run_persists_row(tmp_path, monkeypatch):
    db_path = tmp_path / "obs.db"
    monkeypatch.setattr(observability, "DB_PATH", db_path)

    row_id = log_gemini_run(
        task="audio_analysis",
        video_id="abc123",
        model="gemini-3.5-flash",
        thinking_level="high",
        input_tokens=11,
        output_tokens=22,
        thinking_tokens=33,
        json_valid=True,
        finish_reason="STOP",
    )
    assert isinstance(row_id, int)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT task, video_id, model, thinking_level, input_tokens, output_tokens, "
            "thinking_tokens, json_valid, finish_reason FROM gemini_runs WHERE id = ?",
            (row_id,),
        ).fetchone()
    assert row == (
        "audio_analysis",
        "abc123",
        "gemini-3.5-flash",
        "high",
        11,
        22,
        33,
        1,
        "STOP",
    )


def test_alog_gemini_run_persists_row(tmp_path, monkeypatch):
    db_path = tmp_path / "obs_async.db"
    monkeypatch.setattr(observability, "DB_PATH", db_path)

    row_id = asyncio.run(alog_gemini_run(task="shorts", model="gemini-3.5-flash", json_valid=False))
    assert isinstance(row_id, int)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT task, model, json_valid FROM gemini_runs WHERE id = ?", (row_id,)).fetchone()
    assert row == ("shorts", "gemini-3.5-flash", 0)


def test_log_gemini_response_extracts_usage_and_finish_reason(tmp_path, monkeypatch):
    db_path = tmp_path / "obs_response.db"
    monkeypatch.setattr(observability, "DB_PATH", db_path)
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=5,
            candidates_token_count=8,
            thoughts_token_count=13,
        ),
        candidates=[SimpleNamespace(finish_reason="MAX_TOKENS")],
    )

    row_id = log_gemini_response(
        response=response,
        task="study",
        model="gemini-3.5-flash",
        duration_ms=1234,
        error="truncated",
    )
    assert isinstance(row_id, int)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT input_tokens, output_tokens, thinking_tokens, total_tokens, "
            "duration_ms, finish_reason, error FROM gemini_runs WHERE id = ?",
            (row_id,),
        ).fetchone()
    assert row == (5, 8, 13, 26, 1234, "MAX_TOKENS", "truncated")
