from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services import gemini_capacity_control as control
from services import livedub_qa as qa


class _Files:
    def __init__(self, *, upload_error: BaseException | None = None, poll_error: BaseException | None = None):
        self.upload_error = upload_error
        self.poll_error = poll_error
        self.upload_calls = 0
        self.get_calls = 0
        self.delete_calls: list[str] = []

    async def upload(self, **_kwargs):
        self.upload_calls += 1
        if self.upload_error is not None:
            raise self.upload_error
        return SimpleNamespace(name="files/qa", state="PROCESSING")

    async def get(self, **_kwargs):
        self.get_calls += 1
        if self.poll_error is not None:
            raise self.poll_error
        return SimpleNamespace(name="files/qa", state="ACTIVE")

    async def delete(self, *, name):
        self.delete_calls.append(name)


def _client(name: str, model_calls: list[str], *, error: BaseException | None = None):
    class _Models:
        async def generate_content(self, **_kwargs):
            model_calls.append(name)
            if error is not None:
                raise error
            return SimpleNamespace(text='{"score":100,"verdict":"ok","issues":[]}')

    return SimpleNamespace(
        name=name,
        aio=SimpleNamespace(models=_Models(), files=_Files()),
    )


def _write_srt(path):
    path.write_text(
        "1\n00:00:00,000 --> 00:00:04,000\nТочный русский текст.\n\n",
        encoding="utf-8",
    )


def test_livedub_qa_503_does_not_expand_by_four_keys(monkeypatch, tmp_path):
    calls: list[str] = []
    clients = [
        _client(f"c{i}", calls, error=RuntimeError("503 UNAVAILABLE: high demand"))
        for i in range(1, 5)
    ]
    srt = tmp_path / "dub.srt"
    video = tmp_path / "dub.mp4"
    _write_srt(srt)
    video.write_bytes(b"video")

    monkeypatch.setattr(qa, "HAS_GEMINI", True)
    monkeypatch.setattr(qa, "GEMINI_CLIENTS", clients)
    monkeypatch.setattr(
        qa,
        "types",
        SimpleNamespace(UploadFileConfig=lambda **kwargs: SimpleNamespace(**kwargs)),
    )
    monkeypatch.setattr(control, "transient_retry_delay", lambda _attempt: 0.0)
    monkeypatch.setattr(control, "note_overload", lambda _delay: None)

    import core.globals as core_globals

    monkeypatch.setattr(core_globals, "make_audio_config", lambda **_kwargs: object())

    result = asyncio.run(
        qa._run_translation_qa_base(
            dub_video_path=video,
            original_audio_path=None,
            ai_data={"main_topic": "Reference topic"},
            duration=60,
            model_name="gemini-3.7-flash",
            dub_srt_path=srt,
        )
    )

    assert result is None
    assert calls == ["c1", "c2", "c3"]


def test_livedub_qa_503_never_triggers_schema_text_fallback(monkeypatch, tmp_path):
    calls: list[str] = []
    client = _client(
        "only",
        calls,
        error=RuntimeError("503 UNAVAILABLE: high demand"),
    )
    srt = tmp_path / "dub.srt"
    video = tmp_path / "dub.mp4"
    _write_srt(srt)
    video.write_bytes(b"video")

    monkeypatch.setattr(qa, "HAS_GEMINI", True)
    monkeypatch.setattr(qa, "GEMINI_CLIENTS", [client])
    monkeypatch.setattr(
        qa,
        "types",
        SimpleNamespace(UploadFileConfig=lambda **kwargs: SimpleNamespace(**kwargs)),
    )
    monkeypatch.setattr(control, "transient_retry_delay", lambda _attempt: 0.0)
    monkeypatch.setattr(control, "note_overload", lambda _delay: None)

    import core.globals as core_globals

    monkeypatch.setattr(core_globals, "make_audio_config", lambda **_kwargs: object())

    result = asyncio.run(
        qa._run_translation_qa_base(
            dub_video_path=video,
            original_audio_path=None,
            ai_data={"main_topic": "Reference topic"},
            duration=60,
            model_name="gemini-3.7-flash",
            dub_srt_path=srt,
        )
    )

    assert result is None
    assert calls == ["only"]


def test_livedub_files_poll_failure_cleans_remote_handle(monkeypatch, tmp_path):
    path = tmp_path / "audio.mp3"
    path.write_bytes(b"audio")
    files = _Files(poll_error=RuntimeError("503 Files API overloaded"))
    client = SimpleNamespace(aio=SimpleNamespace(files=files))
    budget = control.GeminiRetryBudget(limit=3)

    monkeypatch.setattr(
        qa,
        "types",
        SimpleNamespace(UploadFileConfig=lambda **kwargs: SimpleNamespace(**kwargs)),
    )
    monkeypatch.setattr(qa.asyncio, "sleep", lambda _delay: asyncio.sleep(0))
    monkeypatch.setattr(control, "transient_retry_delay", lambda _attempt: 0.0)
    monkeypatch.setattr(control, "note_overload", lambda _delay: None)

    with pytest.raises(RuntimeError, match="503"):
        asyncio.run(qa._upload_and_wait(client, path, "qa", budget))

    assert files.upload_calls == 1
    assert files.get_calls == 1
    assert files.delete_calls == ["files/qa"]
