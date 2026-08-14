#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services import gemini36_factory_resilience as resilience


def test_analysis_audio_defaults_are_quality_conservative(monkeypatch):
    monkeypatch.delenv("SHORTS_FACTORY_GEMINI_AUDIO_BITRATE_KBPS", raising=False)
    monkeypatch.delenv("SHORTS_FACTORY_GEMINI_AUDIO_SAMPLE_RATE", raising=False)
    assert resilience.gemini_analysis_bitrate_kbps() == 128
    assert resilience.gemini_analysis_sample_rate() == 48000


def test_analysis_audio_never_accepts_low_fidelity_env(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_GEMINI_AUDIO_BITRATE_KBPS", "32")
    monkeypatch.setenv("SHORTS_FACTORY_GEMINI_AUDIO_SAMPLE_RATE", "8000")
    assert resilience.gemini_analysis_bitrate_kbps() == 96
    assert resilience.gemini_analysis_sample_rate() == 24000


def test_priority_service_tier_is_explicit_and_fail_closed(monkeypatch):
    monkeypatch.delenv("GEMINI_SERVICE_TIER", raising=False)
    assert resilience.configured_service_tier() == "standard"
    monkeypatch.setenv("GEMINI_SERVICE_TIER", "priority")
    assert resilience.configured_service_tier() == "priority"
    monkeypatch.setenv("GEMINI_SERVICE_TIER", "flex")
    with pytest.raises(RuntimeError, match="standard.*priority"):
        resilience.configured_service_tier()


def test_priority_preserves_existing_generate_config():
    original = {"max_output_tokens": 12000, "temperature": 0.1}
    configured = resilience._with_service_tier(original, "priority")
    assert configured == {
        "max_output_tokens": 12000,
        "temperature": 0.1,
        "service_tier": "priority",
    }
    assert original == {"max_output_tokens": 12000, "temperature": 0.1}


def test_compact_analysis_audio_is_aac_mono_and_source_is_not_render_media(
    monkeypatch, tmp_path
):
    import services.shorts_factory_source as source

    source_file = tmp_path / "video_factory_audio_source.webm"
    source_file.write_bytes(b"source" * 500)
    source_probe = SimpleNamespace(
        duration=3263.68,
        has_audio=True,
        audio_sample_rate=48000,
        audio_codec="opus",
    )
    final_probe = SimpleNamespace(
        duration=3263.66,
        has_audio=True,
        audio_sample_rate=48000,
        audio_codec="aac",
    )
    commands: list[list[str]] = []

    async def fake_run(command, *, timeout, text):
        commands.append(list(command))
        output = tmp_path / "video_factory_audio_gemini.aac"
        output.write_bytes(b"aac" * 1000)
        return SimpleNamespace(returncode=0, stderr="")

    async def fake_probe(path):
        return final_probe

    monkeypatch.setattr(source, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(source, "run_cancellable_process", fake_run)
    monkeypatch.setattr(source, "probe_media_async", fake_probe)
    monkeypatch.setattr(resilience.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.delenv("SHORTS_FACTORY_GEMINI_AUDIO_BITRATE_KBPS", raising=False)
    monkeypatch.delenv("SHORTS_FACTORY_GEMINI_AUDIO_SAMPLE_RATE", raising=False)

    result = asyncio.run(
        resilience._prepare_compact_gemini_audio(
            source_file,
            source_probe,
            "video",
        )
    )

    assert result == tmp_path / "video_factory_audio_gemini.aac"
    assert result.exists()
    assert not source_file.exists()
    assert len(commands) == 1
    command = commands[0]
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-b:a") + 1] == "128k"
    assert command[command.index("-ac") + 1] == "1"
    assert command[command.index("-ar") + 1] == "48000"
    assert command[command.index("-f") + 1] == "adts"
    assert "flac" not in command


def test_capacity_defaults_are_bounded_not_sdk_retry_cascade():
    import services.shorts_factory_capacity_runtime as capacity

    assert capacity._FACTORY_CAPACITY_PASS_ATTEMPTS == 4
    assert capacity._FACTORY_CAPACITY_RETRY_BASE_SECONDS == 3.0
    assert capacity._FACTORY_CAPACITY_RETRY_MAX_SECONDS == 20.0
    assert capacity._FACTORY_CAPACITY_RETRY_JITTER_SECONDS == 2.0
