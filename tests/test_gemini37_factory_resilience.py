#!/usr/bin/env python3
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import services.shorts_factory_capacity as capacity
import services.shorts_factory_source as source


def test_analysis_audio_defaults_are_quality_conservative(monkeypatch):
    monkeypatch.delenv("SHORTS_FACTORY_GEMINI_AUDIO_BITRATE_KBPS", raising=False)
    monkeypatch.delenv("SHORTS_FACTORY_GEMINI_AUDIO_SAMPLE_RATE", raising=False)
    assert source.gemini_analysis_bitrate_kbps() == 128
    assert source.gemini_analysis_sample_rate() == 48000


def test_analysis_audio_never_accepts_low_fidelity_env(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_GEMINI_AUDIO_BITRATE_KBPS", "32")
    monkeypatch.setenv("SHORTS_FACTORY_GEMINI_AUDIO_SAMPLE_RATE", "8000")
    assert source.gemini_analysis_bitrate_kbps() == 96
    assert source.gemini_analysis_sample_rate() == 24000


def test_priority_service_tier_is_explicit_and_fail_closed(monkeypatch):
    from core import globals as core_globals

    monkeypatch.delenv("GEMINI_SERVICE_TIER", raising=False)
    assert core_globals.configured_gemini_service_tier() == "standard"
    monkeypatch.setenv("GEMINI_SERVICE_TIER", "priority")
    assert core_globals.configured_gemini_service_tier() == "priority"
    monkeypatch.setenv("GEMINI_SERVICE_TIER", "flex")
    with pytest.raises(RuntimeError, match="standard.*priority"):
        core_globals.configured_gemini_service_tier()


def test_user_visible_publication_owner_uses_only_37_even_with_stale_env(monkeypatch):
    import services.livedub_publication_core as publication

    monkeypatch.setenv("LIVEDUB_INFO_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("LIVEDUB_PUBLICATION_FALLBACK_MODELS", "gemini-3.5-flash")
    assert publication.publication_models() == ["gemini-3.7-flash"]


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


def test_compact_analysis_audio_is_aac_mono_and_source_is_not_render_media(
    monkeypatch, tmp_path
):
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

    async def fake_probe(_path):
        return final_probe

    monkeypatch.setattr(source, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(source, "run_cancellable_process", fake_run)
    monkeypatch.setattr(source, "probe_media_async", fake_probe)
    monkeypatch.setattr(source.shutil, "which", lambda name: "ffmpeg")
    monkeypatch.delenv("SHORTS_FACTORY_GEMINI_AUDIO_BITRATE_KBPS", raising=False)
    monkeypatch.delenv("SHORTS_FACTORY_GEMINI_AUDIO_SAMPLE_RATE", raising=False)

    result = asyncio.run(source._prepare_gemini_audio(source_file, source_probe, "video"))

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
    import services.shorts_factory_capacity_runtime as runtime

    assert runtime._FACTORY_CAPACITY_PASS_ATTEMPTS == 4
    assert runtime._FACTORY_CAPACITY_RETRY_BASE_SECONDS == 3.0
    assert runtime._FACTORY_CAPACITY_RETRY_MAX_SECONDS == 20.0
    assert runtime._FACTORY_CAPACITY_RETRY_JITTER_SECONDS == 2.0
