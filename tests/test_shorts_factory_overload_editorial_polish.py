from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import services.shorts_factory_capacity as capacity
import services.shorts_factory_capacity_runtime as capacity_runtime
import services.shorts_factory_editorial_bridge as bridge
import services.shorts_factory_retry_cache as retry_cache


class _HttpError(RuntimeError):
    def __init__(self, code: int, message: str = "") -> None:
        super().__init__(message or str(code))
        self.code = code


def test_factory_only_clients_disable_hidden_sdk_retries(monkeypatch):
    from core import globals as core_globals

    created = []

    class Retry:
        def __init__(self, *, attempts):
            self.attempts = attempts

    class Http:
        def __init__(self, *, timeout, retry_options):
            self.timeout = timeout
            self.retry_options = retry_options

    def client(**kwargs):
        created.append(kwargs)
        return kwargs

    monkeypatch.setattr(core_globals, "HAS_GEMINI", True)
    monkeypatch.setattr(core_globals, "genai", SimpleNamespace(Client=client))
    monkeypatch.setattr(
        core_globals,
        "types",
        SimpleNamespace(HttpRetryOptions=Retry, HttpOptions=Http),
    )
    monkeypatch.setattr(core_globals, "GEMINI_API_KEY", "k1")
    monkeypatch.setattr(core_globals, "GEMINI_API_KEY_2", "k2")
    monkeypatch.setattr(core_globals, "GEMINI_API_KEY_3", "")
    monkeypatch.setattr(core_globals, "GEMINI_API_KEY_4", "")

    clients = capacity.factory_gemini_clients()
    assert len(clients) == 2
    assert [item["api_key"] for item in created] == ["k1", "k2"]
    assert all(item["http_options"].timeout == 900_000 for item in created)
    assert all(item["http_options"].retry_options.attempts == 1 for item in created)


def test_factory_overload_classifier_matches_real_503_shape():
    error = _HttpError(
        503,
        "503 UNAVAILABLE: This model is currently experiencing high demand",
    )
    assert capacity.factory_retryable_service_error(error) is True
    assert capacity.factory_overload_error(error) is True
    assert capacity.factory_retryable_service_error(ValueError("bad json")) is False
    assert capacity.factory_overload_error(ValueError("bad json")) is False


@pytest.mark.asyncio
async def test_factory_three_pass_resume_keeps_quality_and_does_not_repeat_scout(
    monkeypatch, tmp_path
):
    import services.shorts_factory_candidates as candidates
    import services.shorts_factory_quality_gate as gate
    import services.shorts_factory_source as source

    audio = tmp_path / "source.aac"
    audio.write_bytes(b"audio" * 1000)
    client1 = object()
    client2 = object()
    monkeypatch.setattr(capacity, "factory_gemini_clients", lambda: [client1, client2])
    monkeypatch.setattr(capacity, "safe_status", lambda *args, **kwargs: asyncio.sleep(0))

    fake_types = SimpleNamespace(
        Part=SimpleNamespace(from_bytes=lambda **kwargs: ("audio", kwargs["mime_type"])),
    )
    monkeypatch.setattr(candidates, "types", fake_types)
    monkeypatch.setattr(candidates, "shorts_factory_model", lambda: "gemini-3.6-flash")
    monkeypatch.setattr(candidates, "_scout_prompt", lambda *args: "scout")
    monkeypatch.setattr(candidates, "_judge_prompt", lambda *args: "judge")
    monkeypatch.setattr(candidates, "_boundary_prompt", lambda *args: "boundary")
    monkeypatch.setattr(source, "_strict_boundary_prompt", lambda prompt: prompt)

    calls = []

    async def run_pass(client, *, model, audio_part, prompt, max_tokens):
        calls.append((client, model, prompt, max_tokens))
        if client is client1 and prompt == "judge":
            raise _HttpError(429, "RESOURCE_EXHAUSTED")
        return {"stage": prompt}

    monkeypatch.setattr(candidates, "_run_pass", run_pass)
    raw_plan = {
        "metadata": {"language": "en"},
        "shorts_candidates": [{
            "start_seconds": 10,
            "end_seconds": 70,
            "title": "Strong",
            "hook": "Hook",
            "reason": "Reason",
            "quality_score": 99,
            "boundary_verified": True,
        }],
        "long_candidates": [],
    }
    monkeypatch.setattr(
        candidates,
        "validate_factory_plan",
        lambda *args, **kwargs: {
            "metadata": dict(raw_plan["metadata"]),
            "shorts_candidates": [dict(raw_plan["shorts_candidates"][0])],
            "long_candidates": [],
        },
    )
    monkeypatch.setattr(gate, "apply_factory_quality_gate", lambda plan: plan)
    monkeypatch.setattr(gate, "validated_factory_plan_language", lambda plan: "en")

    result = await capacity_runtime.create_factory_plan_resumable(
        audio,
        title="Title",
        performer="Speaker",
        duration=3600,
        source_language="en",
    )

    assert result["model"] == "gemini-3.6-flash"
    assert result["thinking_level"] == "high"
    assert result["review_passes"] == 3
    assert [(prompt, tokens) for _c, _m, prompt, tokens in calls] == [
        ("scout", 32000),
        ("judge", 28000),
        ("judge", 28000),
        ("boundary", 28000),
    ]
    assert sum(1 for _c, _m, prompt, _t in calls if prompt == "scout") == 1


@pytest.mark.asyncio
async def test_retry_cache_reuses_exact_bytes(monkeypatch, tmp_path):
    import services.media_delivery_probe as media_probe
    import services.shorts_factory_source as source_module

    cache = tmp_path / "cache"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(retry_cache, "FACTORY_CACHE_DIR", cache)
    monkeypatch.setattr(retry_cache, "DOWNLOAD_DIR", downloads)
    monkeypatch.setattr(retry_cache, "cache_ttl_seconds", lambda: 3600.0)
    monkeypatch.setattr(retry_cache, "cache_max_items", lambda: 2)

    probe = SimpleNamespace(
        duration=123.0,
        has_audio=True,
        audio_sample_rate=48000,
        audio_codec="aac",
    )

    async def probe_async(_path):
        return probe

    monkeypatch.setattr(media_probe, "probe_media_async", probe_async)
    monkeypatch.setattr(source_module, "factory_audio_probe_is_usable", lambda value: value is probe)

    source = downloads / "first.aac"
    payload = b"analysis-bytes" * 1000
    source.write_bytes(payload)
    await retry_cache._store_analysis_audio("https://example/video", "media", source)
    reused = await retry_cache._cached_analysis_audio("https://example/video", "media")

    assert reused is not None
    assert reused.read_bytes() == payload
    assert reused != source


def test_copy_fallback_removes_partial_destination(monkeypatch, tmp_path):
    source = tmp_path / "source.aac"
    destination = tmp_path / "destination.aac"
    source.write_bytes(b"abcdef")
    monkeypatch.setattr(
        os,
        "link",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("cross-device")),
    )

    def fail_copy(src, dst, *, length):
        dst.write(b"partial")
        raise OSError("disk failure")

    monkeypatch.setattr(retry_cache.shutil, "copyfileobj", fail_copy)
    with pytest.raises(OSError, match="disk failure"):
        retry_cache.copy_or_link(source, destination)
    assert not destination.exists()


def test_role_policy_is_explicit_for_short_and_long(monkeypatch):
    import services.shorts_factory_candidates as candidates
    import services.shorts_factory_timing as timing

    state = {
        "ru_boundary_evidence": {
            "intervals": [(0.0, 1000.0)],
            "delay_seconds": 0.6,
            "source_speech_intervals": [],
            "source_speech_proof": "none",
            "proof": "proof",
        },
        "plan": {"metadata": {}, "shorts_candidates": [], "long_candidates": []},
        "title": "T",
        "performer": "P",
        "ai_data_holder": {"real_title": "T", "real_author": "P"},
        "aligned": {},
    }
    calls = []

    def align(items, **kwargs):
        calls.append(kwargs["candidate_kind"])
        return [dict(item) for item in items]

    monkeypatch.setattr(timing, "align_candidates_to_ru_speech", align)
    monkeypatch.setattr(candidates, "factory_ai_data", lambda plan, **kwargs: {"plan": plan})
    token = bridge.JOB_STATE.set(state)
    try:
        bridge.role_aware_factory_alignment(
            [{"start_seconds": 10.0, "end_seconds": 70.0}],
            source_duration=1000,
        )
        bridge.role_aware_factory_alignment(
            [{"start_seconds": 100.0, "end_seconds": 700.0}],
            source_duration=1000,
        )
    finally:
        bridge.JOB_STATE.reset(token)

    assert calls == ["short", "long"]
    assert state["ai_data_holder"]["plan"]["shorts_candidates"][0]["start_seconds"] == 10.0
    assert state["ai_data_holder"]["plan"]["long_candidates"][0]["end_seconds"] == 700.0


@pytest.mark.asyncio
async def test_boundary_evidence_starts_before_master_prepare_finishes(
    monkeypatch, tmp_path
):
    import services.livedub_ru_provenance as provenance
    import services.shorts_factory_timing as timing

    evidence_started = asyncio.Event()
    allow_master_finish = asyncio.Event()
    exact_ru = tmp_path / "ru.mp3"
    exact_ru.write_bytes(b"ru")
    translated = tmp_path / "translated.mp4"
    translated.write_bytes(b"video")
    monkeypatch.setattr(provenance, "read_ru_audio_provenance", lambda workdir: exact_ru)

    async def prepare_evidence(**kwargs):
        evidence_started.set()
        await allow_master_finish.wait()
        return {
            "intervals": [(0.0, 100.0)],
            "delay_seconds": 0.6,
            "source_speech_intervals": [],
            "source_speech_proof": "none",
            "proof": "proof",
        }

    monkeypatch.setattr(timing, "prepare_factory_ru_boundary_evidence", prepare_evidence)

    async def original_prepare(*args, **kwargs):
        await asyncio.wait_for(evidence_started.wait(), timeout=1.0)
        allow_master_finish.set()
        return translated

    state = {}
    token = bridge.JOB_STATE.set(state)
    try:
        result = await bridge.translation_video_with_boundary_evidence(
            "https://example/video",
            tmp_path,
            100,
            "en",
            original_prepare=original_prepare,
        )
    finally:
        bridge.JOB_STATE.reset(token)

    assert result == translated
    assert state["ru_boundary_evidence"]["proof"] == "proof"


def test_editorial_bridge_has_no_ambient_status_or_deleted_runtime_import():
    source = Path(bridge.__file__).read_text(encoding="utf-8")
    assert "STATUS_MESSAGE" not in source
    assert "shorts_factory_overload_runtime" not in source
    assert "status_msg=status_msg" in source
