"""Executed Synopsis audio upload/reuse boundaries, including worker-side file I/O."""

import pathlib
import pytest

import asyncio
from types import SimpleNamespace
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from services import telegraph


class _FakeFiles:
    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []

    async def upload(self, **kwargs: Any) -> SimpleNamespace:
        self.uploads.append(kwargs)
        return SimpleNamespace(name="uploaded-audio", state="ACTIVE")


class _FakeModels:
    def __init__(self, responses: Iterable[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        text = self._responses.pop(0) if self._responses else ""
        return SimpleNamespace(text=text)


class _FakeClient:
    def __init__(self, responses: Iterable[str] = ("",)) -> None:
        self.models = _FakeModels(responses)
        self.files = _FakeFiles()
        self.aio = SimpleNamespace(models=self.models, files=self.files)


def _prepare_common(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> tuple[list[tuple[Any, str]], list[float]]:
    deleted: list[tuple[Any, str]] = []
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    async def log_run(**_kwargs: Any) -> None:
        return None

    async def generate(_clients: Any, callback: Callable[[Any], Awaitable[Any]], model_name: str) -> Any:
        return await callback(client)

    monkeypatch.setattr(telegraph, "GEMINI_CLIENTS", [client])
    monkeypatch.setattr(telegraph.asyncio, "sleep", sleep)
    monkeypatch.setattr(telegraph, "alog_gemini_run", log_run)
    monkeypatch.setattr(telegraph, "gemini_generate", generate)
    monkeypatch.setattr(telegraph, "fallback_model", lambda: "")
    monkeypatch.setattr(telegraph, "_spawn_safe_delete", lambda owner, name: deleted.append((owner, name)))
    return deleted, sleeps


def _create_synopsis(path: pathlib.Path, **kwargs: Any) -> tuple[str | None, list[Any] | None]:
    return asyncio.run(
        telegraph.create_telegraph_synopsis(
            path,
            "Title",
            "Author",
            60,
            ai_data={},
            **kwargs,
        )
    )


def test_synopsis_reuses_existing_audio_part_without_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"existing-audio")
    client = _FakeClient()
    existing_part = object()
    deleted, _sleeps = _prepare_common(monkeypatch, client)

    assert _create_synopsis(path, existing_audio_part=existing_part, existing_client=client) == (None, None)

    assert client.models.calls[0]["contents"][0] is existing_part
    assert client.files.uploads == []
    assert deleted == []


def test_synopsis_small_file_uses_inline_part_without_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    path = tmp_path / "audio.mp3"
    payload = b"small-mp3-payload"
    path.write_bytes(payload)
    client = _FakeClient()
    deleted, _sleeps = _prepare_common(monkeypatch, client)

    assert _create_synopsis(path) == (None, None)

    audio_part = client.models.calls[0]["contents"][0]
    assert audio_part.inline_data.data == payload
    assert audio_part.inline_data.mime_type == "audio/mpeg"
    assert client.files.uploads == []
    assert deleted == []


def test_synopsis_large_file_uploads_and_cleans_up_owned_remote_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    path = tmp_path / "large.mp3"
    with path.open("wb") as stream:
        stream.truncate(21 * 1024 * 1024)
    client = _FakeClient()
    deleted, _sleeps = _prepare_common(monkeypatch, client)

    assert _create_synopsis(path) == (None, None)

    assert client.models.calls[0]["contents"][0].name == "uploaded-audio"
    assert len(client.files.uploads) == 1
    upload = client.files.uploads[0]
    assert upload["file"] == path
    assert upload["config"].mime_type == "audio/mpeg"
    assert deleted == [(client, "uploaded-audio")]


def test_synopsis_broken_json_retry_reuses_inline_audio_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    path = tmp_path / "audio.mp3"
    payload = b"retry-audio"
    path.write_bytes(payload)
    client = _FakeClient(['{"outline":', '{"outline": [], "sections": []}'])
    deleted, sleeps = _prepare_common(monkeypatch, client)

    assert _create_synopsis(path) == (None, None)

    assert len(client.models.calls) == 2
    for call in client.models.calls:
        audio_part = call["contents"][0]
        assert audio_part.inline_data.data == payload
        assert audio_part.inline_data.mime_type == "audio/mpeg"
    assert client.files.uploads == []
    assert 10 in sleeps
    assert deleted == []
