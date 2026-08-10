from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import services.shorts_factory_editorial_bridge as bridge
import services.shorts_factory_overload_editorial_polish as polish
import services.shorts_factory_overload_runtime as overload


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

    clients = overload.factory_gemini_clients()

    assert len(clients) == 2
    assert [item["api_key"] for item in created] == ["k1", "k2"]
    assert all(item["http_options"].timeout == 900_000 for item in created)
    assert all(item["http_options"].retry_options.attempts == 1 for item in created)


def test_factory_overload_classifier_matches_real_503_shape():
    error = _HttpError(
        503,
        "503 UNAVAILABLE: This model is currently experiencing high demand",
    )
    assert overload.factory_retryable_service_error(error) is True
    assert overload.factory_overload_error(error) is True
    assert overload.factory_retryable_service_error(ValueError("bad json")) is False
    assert overload.factory_overload_error(ValueError("bad json")) is False


@pytest.mark.asyncio
async def test_factory_three_pass_resume_keeps_quality_and_does_not_repeat_scout(
    monkeypatch, tmp_path
):
    import services.shorts_factory_candidates as candidates
    import services.shorts_factory_quality_gate as gate

    audio = tmp_path / "source.flac"
    audio.write_bytes(b"audio" * 1000)
    client1 = object()
    client2 = object()
    monkeypatch.setattr(overload, "factory_gemini_clients", lambda: [client1, client2])

    fake_types = SimpleNamespace(
        Part=SimpleNamespace(from_bytes=lambda **kwargs: ("audio", kwargs["mime_type"])),
    )
    monkeypatch.setattr(candidates, "types", fake_types)
    monkeypatch.setattr(candidates, "shorts_factory_model", lambda: "gemini-3.6-flash")
    monkeypatch.setattr(candidates, "_scout_prompt", lambda *args: "scout")
    monkeypatch.setattr(candidates, "_judge_prompt", lambda *args: "judge")
    monkeypatch.setattr(candidates, "_boundary_prompt", lambda *args: "boundary")

    calls = []

    async def run_pass(client, *, model, audio_part, prompt, max_tokens):
        calls.append((client, model, prompt, max_tokens))
        if client is client1 and prompt == "judge":
            raise _HttpError(503, "UNAVAILABLE high demand")
        return {"stage": prompt}

    monkeypatch.setattr(candidates, "_run_pass", run_pass)
    raw_plan = {
        "metadata": {"language": "en"},
        "shorts_candidates": [
            {
                "start_seconds": 10,
                "end_seconds": 70,
                "title": "Strong",
                "hook": "Hook",
                "reason": "Reason",
                "quality_score": 99,
                "boundary_verified": True,
            }
        ],
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

    result = await overload.create_factory_plan_resumable(
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
async def test_lossless_retry_cache_reuses_exact_bytes(monkeypatch, tmp_path):
    import services.media_delivery_probe as media_probe
    import services.shorts_factory_source as source_module

    cache = tmp_path / "cache"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr(overload, "FACTORY_CACHE_DIR", cache)
    monkeypatch.setattr(overload, "DOWNLOAD_DIR", downloads)
    monkeypatch.setattr(overload, "cache_ttl_seconds", lambda: 3600.0)
    monkeypatch.setattr(overload, "cache_max_items", lambda: 2)

    probe = SimpleNamespace(
        duration=123.0,
        has_audio=True,
        audio_sample_rate=48000,
        audio_codec="flac",
    )

    async def probe_async(_path):
        return probe

    monkeypatch.setattr(media_probe, "probe_media_async", probe_async)
    monkeypatch.setattr(
        source_module,
        "factory_audio_probe_is_usable",
        lambda value: value is probe,
    )

    source = downloads / "first.flac"
    payload = b"lossless-bytes" * 1000
    source.write_bytes(payload)
    await overload._store_analysis_audio("https://example/video", "media", source)
    reused = await overload._cached_analysis_audio("https://example/video", "media")

    assert reused is not None
    assert reused.read_bytes() == payload
    assert reused != source


def test_copy_fallback_removes_partial_destination(monkeypatch, tmp_path):
    source = tmp_path / "source.flac"
    destination = tmp_path / "destination.flac"
    source.write_bytes(b"abcdef")

    monkeypatch.setattr(
        os,
        "link",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("cross-device")),
    )

    def fail_copy(src, dst, *, length):
        dst.write(b"partial")
        raise OSError("disk failure")

    monkeypatch.setattr(overload.shutil, "copyfileobj", fail_copy)

    with pytest.raises(OSError, match="disk failure"):
        overload.copy_or_link(source, destination)
    assert not destination.exists()


def test_capture_factory_ai_data_never_blanks_render_metadata():
    state = {}
    expected = {
        "format": "sermon",
        "real_title": "Title",
        "real_author": "Speaker",
        "analysis_summary": "summary",
    }

    def original(plan, *, title, performer):
        assert plan["metadata"]["language"] == "ru"
        assert title == "Title"
        assert performer == "Speaker"
        return expected

    token = bridge.JOB_STATE.set(state)
    try:
        result = polish.capture_factory_ai_data(
            original,
            {"metadata": {"language": "ru"}},
            title="Title",
            performer="Speaker",
        )
    finally:
        bridge.JOB_STATE.reset(token)

    assert result is expected
    assert state["ai_data_holder"] == expected
    assert state["ai_data_holder"] is not expected
    assert state["aligned"] == {}


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


def test_ambiguous_alignment_role_fails_closed():
    state = {"aligned": {}, "ru_boundary_evidence": {}}
    token = bridge.JOB_STATE.set(state)
    try:
        with pytest.raises(RuntimeError, match="ambiguous"):
            bridge.role_aware_factory_alignment(
                [{"start_seconds": 0.0, "end_seconds": 250.0}],
                source_duration=1000,
            )
    finally:
        bridge.JOB_STATE.reset(token)


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

    monkeypatch.setattr(
        provenance,
        "read_ru_audio_provenance",
        lambda workdir: exact_ru,
    )

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


def test_russian_source_does_not_create_pending_editorial_copy(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    pending = tmp_path / "pending"
    monkeypatch.setattr(bridge, "PENDING_DIR", pending)

    state = {
        "plan": {"metadata": {"language": "ru"}},
        "source_language": "ru",
    }
    token = bridge.JOB_STATE.set(state)
    try:
        result = bridge.persist_source_for_editorial(
            source,
            "media",
            original_persist=lambda path, media_id: path,
        )
    finally:
        bridge.JOB_STATE.reset(token)

    assert result == source
    assert not pending.exists() or not list(pending.iterdir())
    assert "editorial_source" not in state


def test_pending_editorial_sources_are_ttl_and_count_bounded(monkeypatch, tmp_path):
    pending = tmp_path / "pending"
    pending.mkdir()
    monkeypatch.setattr(bridge, "PENDING_DIR", pending)
    monkeypatch.setattr(bridge, "cache_ttl_seconds", lambda: 100.0)
    monkeypatch.setattr(bridge, "cache_max_items", lambda: 2)
    monkeypatch.setattr(bridge.time, "time", lambda: 1000.0)

    stamps = {
        "expired.mp4": 850.0,
        "oldest_valid.mp4": 920.0,
        "middle.mp4": 950.0,
        "newest.mp4": 990.0,
    }
    for name, modified in stamps.items():
        path = pending / name
        path.write_bytes(b"video")
        os.utime(path, (modified, modified))

    bridge.cleanup_pending_sources()

    assert sorted(path.name for path in pending.iterdir()) == [
        "middle.mp4",
        "newest.mp4",
    ]


@pytest.mark.asyncio
async def test_successful_factory_runs_editorial_after_delivery(monkeypatch):
    seen = []

    async def original(*args, **kwargs):
        return True

    async def editorial(**kwargs):
        seen.append(kwargs["url"])

    monkeypatch.setattr(bridge, "_send_editorial_after_factory", editorial)
    update = SimpleNamespace(message=SimpleNamespace())
    result = await bridge.process_factory_with_editorial(
        original,
        "https://example/video",
        update,
        silent_errors=True,
    )

    assert result is True
    assert seen == ["https://example/video"]


@pytest.mark.asyncio
async def test_editorial_only_uses_actual_yandex_master_duration_and_no_planner(
    monkeypatch, tmp_path
):
    import pipelines.shorts_factory as factory
    import services.media_delivery_probe as media_probe
    import services.shorts_factory_execution_guard as guard
    import services.shorts_factory_source as source
    import services.shorts_video_impl as shorts_video
    import services.translation_editorial_factory as editorial
    from services.media_delivery_probe import MediaProbe

    async def info(_url):
        return {
            "id": "abc12345678",
            "duration": 120,
            "language": "en",
            "title": "Speaker - Title",
            "channel": "Speaker",
        }

    monkeypatch.setattr(factory, "_load_video_info", info)
    monkeypatch.setattr(factory, "_media_id", lambda info, url: "abc12345678")
    monkeypatch.setattr(shorts_video, "HAS_FASTER_WHISPER", True)
    monkeypatch.setattr(guard, "factory_preflight_issues", lambda **kwargs: [])
    monkeypatch.setattr(guard, "enforce_factory_translation_preflight", lambda: None)
    monkeypatch.setattr(
        bridge.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=20 * 1024**3),
    )
    monkeypatch.setattr(bridge.shutil, "which", lambda name: f"/{name}")

    translated = tmp_path / "translated.mp4"
    translated.write_bytes(b"video")

    async def prepare_video(*args, **kwargs):
        return translated

    actual_probe = MediaProbe(
        duration=121.6,
        width=1920,
        height=1080,
        audio_sample_rate=48000,
        audio_codec="aac",
        has_video=True,
        has_audio=True,
    )

    async def probe_async(path):
        assert Path(path) == translated
        return actual_probe

    captured = {}

    async def prepare_review(**kwargs):
        captured.update(kwargs)
        return tmp_path / "pack.zip", None, None

    async def send_review(*args, **kwargs):
        return None

    monkeypatch.setattr(source, "prepare_factory_translation_video", prepare_video)
    monkeypatch.setattr(media_probe, "probe_media_async", probe_async)
    monkeypatch.setattr(editorial, "prepare_factory_editorial_review", prepare_review)
    monkeypatch.setattr(editorial, "send_factory_editorial_files", send_review)

    async def forbidden_planner(*args, **kwargs):
        raise AssertionError("Gemini Factory planner must not run in editorial-only mode")

    monkeypatch.setattr(factory, "create_factory_plan", forbidden_planner)

    class Message:
        async def reply_text(self, text):
            return self

        async def edit_text(self, text):
            return None

    update = SimpleNamespace(message=Message())
    assert await bridge.process_translation_editorial_only(
        "https://example/video", update, silent_errors=True
    ) is True
    assert captured["duration"] == pytest.approx(121.6)
    assert captured["shorts_candidates"] == []
    assert captured["long_candidates"] == []


def test_runtime_manifest_requires_polish_after_factory():
    import services.runtime_manifest as manifest

    features = list(manifest.DEFAULT_RUNTIME_FEATURES)
    index = next(
        i
        for i, feature in enumerate(features)
        if feature.feature_id == "shorts-factory-overload-editorial-polish"
    )
    assert features[index - 1].feature_id == "shorts-factory-max"
    assert features[index].required is True
    assert features[index].dependencies == ("shorts-factory-max",)
