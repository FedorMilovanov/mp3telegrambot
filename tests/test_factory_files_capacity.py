from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services import gemini_capacity_control as control
from services import shorts_factory_capacity as capacity
from services import shorts_factory_capacity_runtime as runtime


class _Files:
    def __init__(self) -> None:
        self.upload_calls = 0
        self.get_calls = 0
        self.deleted: list[str] = []

    async def upload(self, **_kwargs):
        self.upload_calls += 1
        return SimpleNamespace(name="files/factory", state="PROCESSING")

    async def get(self, **_kwargs):
        self.get_calls += 1
        raise RuntimeError("503 Files API overloaded")

    async def delete(self, *, name):
        self.deleted.append(name)


def test_factory_poll_503_cleans_remote_handle_and_stays_in_files_domain(
    monkeypatch,
    tmp_path,
):
    import services.shorts_factory_candidates as candidates
    import services.shorts_factory_source as source

    audio = tmp_path / "factory.aac"
    with audio.open("wb") as stream:
        stream.truncate(19 * 1024 * 1024)

    files = _Files()
    client = SimpleNamespace(aio=SimpleNamespace(files=files))

    async def verified_duration(_path):
        return 120.0

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(runtime.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(control, "transient_retry_delay", lambda _attempt: 0.0)
    monkeypatch.setattr(control, "note_overload", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(capacity, "factory_gemini_clients", lambda: [client])
    monkeypatch.setattr(
        candidates,
        "types",
        SimpleNamespace(
            UploadFileConfig=lambda **kwargs: SimpleNamespace(**kwargs),
            Part=SimpleNamespace(from_bytes=lambda **kwargs: SimpleNamespace(**kwargs)),
        ),
    )
    monkeypatch.setattr(source, "factory_audio_mime_type", lambda _path: "audio/aac")
    monkeypatch.setattr(source, "measure_factory_audio_duration", verified_duration)
    monkeypatch.setattr(
        source,
        "factory_duration_matches",
        lambda actual, expected: abs(float(actual) - float(expected)) <= 2.0,
    )

    with pytest.raises(RuntimeError):
        asyncio.run(
            runtime.create_factory_plan_resumable(
                audio,
                title="Title",
                performer="Author",
                duration=120,
            )
        )

    assert files.upload_calls == 1
    assert files.get_calls == 1
    assert files.deleted == ["files/factory"]
