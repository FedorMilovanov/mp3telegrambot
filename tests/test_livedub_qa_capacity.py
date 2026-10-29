from __future__ import annotations
import pathlib

import asyncio
from typing import Any
from types import SimpleNamespace

import pytest

from services import gemini_capacity_control as control
from services import livedub_qa as qa


class _Files:
    def __init__(
        self,
        *,
        upload_error: BaseException | None = None,
        poll_error: BaseException | None = None,
        delete_error: BaseException | None = None,
    ) -> None:
        self.upload_error = upload_error
        self.poll_error = poll_error
        self.delete_error = delete_error
        self.upload_calls = 0
        self.get_calls = 0
        self.delete_calls: list[str] = []

    async def upload(self, **_kwargs: Any) -> SimpleNamespace:
        self.upload_calls += 1
        if self.upload_error is not None:
            raise self.upload_error
        return SimpleNamespace(name="files/qa", state="PROCESSING")

    async def get(self, **_kwargs: Any) -> SimpleNamespace:
        self.get_calls += 1
        if self.poll_error is not None:
            raise self.poll_error
        return SimpleNamespace(name="files/qa", state="ACTIVE")

    async def delete(self, *, name: str) -> None:
        self.delete_calls.append(name)
        if self.delete_error is not None:
            raise self.delete_error


def _client(name: str, model_calls: list[str], *, error: BaseException | None = None) -> SimpleNamespace:
    class _Models:
        async def generate_content(self, **_kwargs: Any) -> SimpleNamespace:
            model_calls.append(name)
            if error is not None:
                raise error
            return SimpleNamespace(text='{"score":100,"verdict":"ok","issues":[]}')

    return SimpleNamespace(
        name=name,
        aio=SimpleNamespace(models=_Models(), files=_Files()),
    )


def _write_srt(path: pathlib.Path) -> None:
    path.write_text(
        "1\n00:00:00,000 --> 00:00:04,000\nТочный русский текст.\n\n",
        encoding="utf-8",
    )


def _install_common(monkeypatch: pytest.MonkeyPatch, clients: list[Any]) -> None:
    monkeypatch.setattr(qa, "HAS_GEMINI", True)
    monkeypatch.setattr(qa, "GEMINI_CLIENTS", clients)
    monkeypatch.setattr(
        qa,
        "types",
        SimpleNamespace(UploadFileConfig=lambda **kwargs: SimpleNamespace(**kwargs)),
    )
    monkeypatch.setattr(control, "transient_retry_delay", lambda _attempt: 0.0)
    monkeypatch.setattr(control, "note_overload", lambda *_args, **_kwargs: None)

    import core.globals as core_globals

    monkeypatch.setattr(core_globals, "make_audio_config", lambda **_kwargs: object())


def test_remote_qa_file_delete_failure_is_nonfatal() -> None:
    files = _Files(delete_error=RuntimeError("Files API unavailable"))
    client = SimpleNamespace(aio=SimpleNamespace(files=files))

    asyncio.run(
        qa._delete_uploaded_qa_file_best_effort(
            client,
            SimpleNamespace(name="files/qa"),
        )
    )

    assert files.delete_calls == ["files/qa"]


def test_malformed_qa_response_logging_failure_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    calls: list[str] = []

    class _Models:
        async def generate_content(self, **_kwargs: Any) -> SimpleNamespace:
            calls.append("only")
            return SimpleNamespace(text="not-json", candidates=[], usage_metadata=None)

    client = SimpleNamespace(
        name="only",
        aio=SimpleNamespace(models=_Models(), files=_Files()),
    )
    srt = tmp_path / "dub.srt"
    video = tmp_path / "dub.mp4"
    _write_srt(srt)
    video.write_bytes(b"video")
    _install_common(monkeypatch, [client])

    def fragile_warning(message: object, *_args: Any) -> None:
        if str(message).startswith("[LiveDubQA] не распарсился"):
            raise RuntimeError("logging unavailable")

    monkeypatch.setattr(qa.logger, "warning", fragile_warning)

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


def test_livedub_qa_503_does_not_expand_by_four_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    calls: list[str] = []
    clients = [_client(f"c{i}", calls, error=RuntimeError("503 UNAVAILABLE: high demand")) for i in range(1, 5)]
    srt = tmp_path / "dub.srt"
    video = tmp_path / "dub.mp4"
    _write_srt(srt)
    video.write_bytes(b"video")
    _install_common(monkeypatch, clients)

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


def test_livedub_qa_503_never_triggers_schema_text_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
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
    _install_common(monkeypatch, [client])

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


def test_key_bound_original_retries_owner_instead_of_dropping_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    calls: list[str] = []
    owner = _client(
        "owner",
        calls,
        error=RuntimeError("503 UNAVAILABLE: high demand"),
    )
    other = _client(
        "other",
        calls,
        error=RuntimeError("503 UNAVAILABLE: high demand"),
    )
    srt = tmp_path / "dub.srt"
    video = tmp_path / "dub.mp4"
    _write_srt(srt)
    video.write_bytes(b"video")
    _install_common(monkeypatch, [owner, other])
    existing = SimpleNamespace(name="files/original", state="ACTIVE")

    result = asyncio.run(
        qa._run_translation_qa_base(
            dub_video_path=video,
            original_audio_path=None,
            ai_data=None,
            duration=60,
            model_name="gemini-3.7-flash",
            dub_srt_path=srt,
            existing_audio_part=existing,
            existing_client=owner,
        )
    )

    assert result is None
    assert calls == ["owner", "owner", "owner"]
    assert other.aio.files.upload_calls == 0


def test_clean_dub_preflight_skips_extraction_and_missing_original(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    calls: list[str] = []
    _install_common(monkeypatch, [_client("unused", calls)])
    video = tmp_path / "dub.mp4"
    clean_dub = tmp_path / "clean-ru.mp3"
    video.write_bytes(b"video")
    clean_dub.write_bytes(b"clean")
    monkeypatch.setattr(
        qa,
        "_extract_audio_for_qa",
        lambda *_args: pytest.fail("existing clean dub must not be extracted again"),
    )

    result = asyncio.run(
        qa._run_translation_qa_base(
            dub_video_path=video,
            original_audio_path=tmp_path / "missing-original.mp3",
            ai_data=None,
            duration=60,
            model_name="gemini-3.7-flash",
            dub_audio_path=clean_dub,
        )
    )

    assert result is None
    assert calls == []


def test_livedub_files_poll_failure_cleans_remote_handle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
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

    async def no_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(qa.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(control, "transient_retry_delay", lambda _attempt: 0.0)
    monkeypatch.setattr(control, "note_overload", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="503"):
        asyncio.run(qa._upload_and_wait(client, path, "qa", budget))

    assert files.upload_calls == 1
    assert files.get_calls == 1
    assert files.delete_calls == ["files/qa"]
