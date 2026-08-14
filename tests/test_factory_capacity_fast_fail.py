#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest

from services import livedub_info_presentation
from services import shorts_factory_capacity_runtime as capacity_runtime


class _ServiceError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class _FakeFiles:
    def __init__(self, owner: str) -> None:
        self.owner = owner
        self.upload_calls = 0
        self.delete_calls = 0

    async def upload(self, *, file, config):
        self.upload_calls += 1
        return SimpleNamespace(name=f"files/{self.owner}")

    async def delete(self, *, name):
        self.delete_calls += 1


def _install_fake_factory_modules(monkeypatch, run_pass):
    import services.shorts_factory_candidates as candidates
    import services.shorts_factory_quality_gate as quality_gate
    import services.shorts_factory_source as source

    monkeypatch.setenv("SHORTS_FACTORY_MODEL", "gemini-3.7-flash")
    monkeypatch.setenv("SHORTS_FACTORY_FALLBACK_MODELS", "gemini-3.6-flash")
    monkeypatch.setattr(
        candidates,
        "types",
        SimpleNamespace(
            Part=SimpleNamespace(
                from_bytes=lambda *, data, mime_type: SimpleNamespace(
                    data=data,
                    mime_type=mime_type,
                )
            ),
            UploadFileConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        ),
    )
    monkeypatch.setattr(candidates, "_run_pass", run_pass)
    monkeypatch.setattr(candidates, "_scout_prompt", lambda *args: "scout")
    monkeypatch.setattr(candidates, "_judge_prompt", lambda *args: "judge")
    monkeypatch.setattr(candidates, "_boundary_prompt", lambda *args: "boundary")
    monkeypatch.setattr(
        candidates,
        "validate_factory_plan",
        lambda *args, **kwargs: {
            "shorts_candidates": [{"start": 1.0, "end": 10.0}],
            "long_candidates": [],
        },
    )
    monkeypatch.setattr(quality_gate, "apply_factory_quality_gate", lambda plan: plan)
    monkeypatch.setattr(
        quality_gate,
        "validated_factory_plan_language",
        lambda plan: "ru",
    )
    monkeypatch.setattr(source, "factory_audio_mime_type", lambda path: "audio/flac")


def _disable_capacity_retry_delay(monkeypatch) -> None:
    monkeypatch.setattr(capacity_runtime, "_capacity_retry_delay", lambda attempt: 0.0)


def test_503_exhausts_37_then_36_and_stops_before_second_client(monkeypatch, tmp_path):
    audio = tmp_path / "factory.flac"
    audio.write_bytes(b"x" * 2048)
    first = SimpleNamespace(name="first")
    second = SimpleNamespace(name="second")
    calls: list[tuple[str, str]] = []

    async def run_pass(client, **kwargs):
        calls.append((client.name, kwargs["model"]))
        raise _ServiceError(503, "UNAVAILABLE: high demand")

    _install_fake_factory_modules(monkeypatch, run_pass)
    _disable_capacity_retry_delay(monkeypatch)
    monkeypatch.setattr(
        capacity_runtime.overload_runtime,
        "factory_gemini_clients",
        lambda: [first, second],
    )

    with pytest.raises(RuntimeError, match="503/high demand") as raised:
        asyncio.run(
            capacity_runtime.create_factory_plan_resumable(
                audio,
                title="Title",
                performer="Author",
                duration=120,
            )
        )

    assert calls == [
        ("first", "gemini-3.7-flash"),
        ("first", "gemini-3.7-flash"),
        ("first", "gemini-3.7-flash"),
        ("first", "gemini-3.6-flash"),
        ("first", "gemini-3.6-flash"),
        ("first", "gemini-3.6-flash"),
    ]
    assert "3.5/Lite не использовались" in str(raised.value)
    assert "retry-кэше" in str(raised.value)


def test_persistent_37_503_falls_to_36_on_same_uploaded_audio(monkeypatch, tmp_path):
    import services.shorts_factory_candidates as candidates

    audio = tmp_path / "factory.flac"
    with audio.open("wb") as stream:
        stream.truncate(19 * 1024 * 1024)

    first_files = _FakeFiles("first")
    second_files = _FakeFiles("second")
    first = SimpleNamespace(name="first", aio=SimpleNamespace(files=first_files))
    second = SimpleNamespace(name="second", aio=SimpleNamespace(files=second_files))
    calls: list[tuple[str, str]] = []
    audio_parts: list[object] = []

    async def run_pass(client, **kwargs):
        model = kwargs["model"]
        calls.append((client.name, model))
        audio_parts.append(kwargs["audio_part"])
        if model == "gemini-3.7-flash":
            raise _ServiceError(503, "UNAVAILABLE: high demand")
        return {"ok": True, "pass": len(calls)}

    async def wait_uploaded_file(client, uploaded):
        return uploaded

    _install_fake_factory_modules(monkeypatch, run_pass)
    _disable_capacity_retry_delay(monkeypatch)
    monkeypatch.setattr(candidates, "_wait_uploaded_file", wait_uploaded_file)
    monkeypatch.setattr(
        capacity_runtime.overload_runtime,
        "factory_gemini_clients",
        lambda: [first, second],
    )

    plan = asyncio.run(
        capacity_runtime.create_factory_plan_resumable(
            audio,
            title="Title",
            performer="Author",
            duration=120,
        )
    )

    assert calls == [
        ("first", "gemini-3.7-flash"),
        ("first", "gemini-3.7-flash"),
        ("first", "gemini-3.7-flash"),
        ("first", "gemini-3.6-flash"),
        ("first", "gemini-3.6-flash"),
        ("first", "gemini-3.6-flash"),
    ]
    assert first_files.upload_calls == 1
    assert first_files.delete_calls == 1
    assert second_files.upload_calls == 0
    assert second_files.delete_calls == 0
    assert audio_parts and all(part is audio_parts[0] for part in audio_parts)
    assert plan["primary_model"] == "gemini-3.7-flash"
    assert plan["model"] == "gemini-3.6-flash"
    assert plan["models_used"] == ["gemini-3.6-flash"]
    assert plan["thinking_level"] == "high"
    assert plan["review_passes"] == 3
    assert plan["strict_quality"] is True
    assert plan["semantic_downgrade"] is False


def test_transient_37_503_recovers_without_fallback(monkeypatch, tmp_path):
    audio = tmp_path / "factory.flac"
    audio.write_bytes(b"x" * 2048)
    first = SimpleNamespace(name="first")
    calls: list[str] = []

    async def run_pass(client, **kwargs):
        calls.append(kwargs["model"])
        if len(calls) == 1:
            raise _ServiceError(503, "UNAVAILABLE: high demand")
        return {"ok": True, "pass": len(calls)}

    _install_fake_factory_modules(monkeypatch, run_pass)
    _disable_capacity_retry_delay(monkeypatch)
    monkeypatch.setattr(
        capacity_runtime.overload_runtime,
        "factory_gemini_clients",
        lambda: [first],
    )

    plan = asyncio.run(
        capacity_runtime.create_factory_plan_resumable(
            audio,
            title="Title",
            performer="Author",
            duration=120,
        )
    )

    assert calls == [
        "gemini-3.7-flash",
        "gemini-3.7-flash",
        "gemini-3.7-flash",
        "gemini-3.7-flash",
    ]
    assert plan["model"] == "gemini-3.7-flash"
    assert plan["primary_model"] == "gemini-3.7-flash"
    assert plan["thinking_level"] == "high"


def test_429_still_rotates_and_keeps_three_pass_high_quality(monkeypatch, tmp_path):
    audio = tmp_path / "factory.flac"
    audio.write_bytes(b"x" * 2048)
    first = SimpleNamespace(name="first")
    second = SimpleNamespace(name="second")
    calls: list[tuple[str, str]] = []

    async def run_pass(client, **kwargs):
        calls.append((client.name, kwargs["model"]))
        if client is first:
            raise _ServiceError(429, "RESOURCE_EXHAUSTED")
        return {"ok": True, "pass": len(calls)}

    _install_fake_factory_modules(monkeypatch, run_pass)
    monkeypatch.setattr(
        capacity_runtime.overload_runtime,
        "factory_gemini_clients",
        lambda: [first, second],
    )

    plan = asyncio.run(
        capacity_runtime.create_factory_plan_resumable(
            audio,
            title="Title",
            performer="Author",
            duration=120,
        )
    )

    assert calls == [
        ("first", "gemini-3.7-flash"),
        ("second", "gemini-3.7-flash"),
        ("second", "gemini-3.7-flash"),
        ("second", "gemini-3.7-flash"),
    ]
    assert plan["model"] == "gemini-3.7-flash"
    assert plan["thinking_level"] == "high"
    assert plan["review_passes"] == 3
    assert plan["strict_quality"] is True


def test_livedub_presentation_preserves_native_all_clients_marker(monkeypatch):
    module = ModuleType("services.livedub_info")

    async def original_build(
        title_line,
        dub_srt_path=None,
        *,
        source_url="",
        force=False,
    ):
        return {
            "youtube_title": "Русское название",
            "telegram_description": "Русское описание",
            "source_url": source_url,
        }

    original_build._mp3bot_all_clients = True
    module.build_livedub_info_card = original_build
    module.safe_trim_caption = lambda text, limit: text[:limit]
    monkeypatch.setitem(sys.modules, "services.livedub_info", module)

    livedub_info_presentation.install_livedub_info_presentation()

    wrapped = module.build_livedub_info_card
    assert getattr(wrapped, "_mp3bot_clean_presentation", False) is True
    assert getattr(wrapped, "_mp3bot_all_clients", False) is True
