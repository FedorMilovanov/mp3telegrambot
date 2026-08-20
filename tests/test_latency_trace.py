from __future__ import annotations

import asyncio
import logging

import pytest

import services.gemini_capacity_control as capacity_control
import services.latency_trace as latency_trace
from services.async_process import _latency_stage


def test_latency_trace_aggregates_one_compact_summary(caplog) -> None:
    caplog.set_level(logging.INFO, logger="services.latency_trace")
    token = latency_trace.begin_latency_trace("rus")
    try:
        latency_trace.record_latency("gemini_inference_roundtrip", 1.25)
        latency_trace.record_latency("gemini_inference_roundtrip", 0.75)
        latency_trace.note_latency_event("gemini_inference_overload")
        trace_id = latency_trace.current_latency_trace_id()
        assert trace_id
    finally:
        summary = latency_trace.finish_latency_trace(token, outcome="ok")

    assert "mode=rus" in summary
    assert "outcome=ok" in summary
    assert "gemini_inference_roundtrip=2.00s/2" in summary
    assert "gemini_inference_overload=count:1" in summary
    assert latency_trace.current_latency_trace_id() == ""
    assert sum("[LATENCY]" in record.getMessage() for record in caplog.records) == 2


@pytest.mark.asyncio
async def test_latency_trace_context_flows_into_child_task() -> None:
    token = latency_trace.begin_latency_trace("shorts_max")

    async def child() -> None:
        await asyncio.sleep(0)
        latency_trace.record_latency("process_ffmpeg", 0.125)

    try:
        await asyncio.create_task(child())
        summary = latency_trace.finish_latency_trace(token, outcome="ok")
    except Exception:
        latency_trace.finish_latency_trace(token, outcome="error")
        raise

    assert "process_ffmpeg=0.12s/1" in summary or "process_ffmpeg=0.13s/1" in summary


@pytest.mark.asyncio
async def test_gemini_capacity_owner_measures_queue_and_roundtrip(monkeypatch) -> None:
    recorded: list[str] = []
    monkeypatch.setattr(
        capacity_control,
        "record_latency",
        lambda stage, _elapsed: recorded.append(stage),
    )

    result = await capacity_control.run_heavy_gemini_call(
        lambda: asyncio.sleep(0, result="ok"),
        domain="inference",
    )

    assert result == "ok"
    assert "gemini_inference_semaphore_wait" in recorded
    assert "gemini_inference_roundtrip" in recorded


def test_subprocess_latency_classifier_is_narrow() -> None:
    assert _latency_stage(["python", "-m", "yt_dlp", "URL"]) == "process_yt_dlp"
    assert _latency_stage(["ffmpeg.exe", "-i", "a.mp3"]) == "process_ffmpeg"
    assert _latency_stage(["ffprobe", "a.mp3"]) == "process_ffprobe"
    assert _latency_stage(["node.exe", "script.js"]) == "process_node"
    assert _latency_stage(["deno.exe", "run", "script.ts"]) == "process_deno"
    assert _latency_stage(["git", "status"]) == "process_other"
