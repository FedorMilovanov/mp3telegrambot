from __future__ import annotations

import asyncio
from types import SimpleNamespace

from services import gemini_analyze as analyze
from services import gemini_capacity_control as control


class _ServiceError(RuntimeError):
    pass


async def _noop_progress(*_args, **_kwargs):
    return None


def _install_common(monkeypatch, tmp_path):
    audio = tmp_path / "analysis.mp3"
    audio.write_bytes(b"x" * 2048)
    monkeypatch.setenv("GEMINI_HEAVY_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("GEMINI_TRANSIENT_MAX_ATTEMPTS", "3")
    monkeypatch.setattr(control, "transient_retry_delay", lambda _attempt: 0.0)
    monkeypatch.setattr(control, "note_overload", lambda _delay: None)
    monkeypatch.setattr(analyze, "set_progress", _noop_progress)
    monkeypatch.setattr(analyze, "GEMINI_MODEL", "gemini-3.7-flash")
    monkeypatch.setattr(analyze, "is_model_exhausted", lambda _model: False)
    monkeypatch.setattr(analyze, "build_audio_analysis_prompt", lambda **_kwargs: "prompt")
    monkeypatch.setattr(
        analyze,
        "compact_prompt_for_generation",
        lambda text: SimpleNamespace(
            text=text,
            saved_chars=0,
            original_chars=len(text),
            compacted_chars=len(text),
            removed_lines=0,
        ),
    )
    monkeypatch.setattr(analyze, "_audio_structured_output_enabled", lambda: False)
    monkeypatch.setattr(analyze, "make_audio_config", lambda **_kwargs: object())
    monkeypatch.setattr(
        analyze,
        "types",
        SimpleNamespace(
            UploadFileConfig=lambda **kwargs: SimpleNamespace(**kwargs),
            Part=SimpleNamespace(from_bytes=lambda **kwargs: SimpleNamespace(**kwargs)),
        ),
    )
    monkeypatch.setattr(analyze, "_spawn_safe_delete", lambda *_args, **_kwargs: None)
    return audio


def test_audio_inference_503_is_capped_at_three_calls_across_four_keys(monkeypatch, tmp_path):
    audio = _install_common(monkeypatch, tmp_path)
    model_calls: list[str] = []
    upload_calls: list[str] = []

    class Files:
        def __init__(self, name: str):
            self.name = name

        async def upload(self, **_kwargs):
            upload_calls.append(self.name)
            return SimpleNamespace(name=f"files/{self.name}", state="ACTIVE")

        async def get(self, **_kwargs):
            raise AssertionError("ACTIVE upload must not be polled")

        async def delete(self, **_kwargs):
            return None

    class Models:
        def __init__(self, name: str):
            self.name = name

        async def generate_content(self, **_kwargs):
            model_calls.append(self.name)
            raise _ServiceError("503 UNAVAILABLE: high demand")

    clients = [
        SimpleNamespace(aio=SimpleNamespace(files=Files(f"k{i}"), models=Models(f"k{i}")))
        for i in range(1, 5)
    ]
    monkeypatch.setattr(analyze, "GEMINI_CLIENTS", clients)

    result = asyncio.run(
        analyze.gemini_analyze_audio(audio, "Title", "Author", 120, None)
    )

    assert result == (None, None, None)
    assert model_calls == ["k1", "k1", "k2"]
    assert upload_calls == ["k1", "k2"]


def test_audio_files_503_is_capped_at_three_uploads_and_never_reaches_inference(monkeypatch, tmp_path):
    audio = _install_common(monkeypatch, tmp_path)
    upload_calls: list[str] = []
    model_calls: list[str] = []

    class Files:
        def __init__(self, name: str):
            self.name = name

        async def upload(self, **_kwargs):
            upload_calls.append(self.name)
            raise _ServiceError("503 UNAVAILABLE: Files API overloaded")

        async def delete(self, **_kwargs):
            return None

    class Models:
        def __init__(self, name: str):
            self.name = name

        async def generate_content(self, **_kwargs):
            model_calls.append(self.name)
            return SimpleNamespace(text="{}", candidates=[])

    clients = [
        SimpleNamespace(aio=SimpleNamespace(files=Files(f"k{i}"), models=Models(f"k{i}")))
        for i in range(1, 5)
    ]
    monkeypatch.setattr(analyze, "GEMINI_CLIENTS", clients)

    result = asyncio.run(
        analyze.gemini_analyze_audio(audio, "Title", "Author", 120, None)
    )

    assert result == (None, None, None)
    assert upload_calls == ["k1", "k2", "k3"]
    assert model_calls == []


def test_audio_low_thinking_recovery_is_not_silently_promoted_to_high():
    from core.globals import _effective_thinking_level

    assert _effective_thinking_level("gemini-3.7-flash", "low") == "low"
