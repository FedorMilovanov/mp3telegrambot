from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from services import ffmpeg
from services import youtube_transcript as transcript


@pytest.mark.asyncio
async def test_transcript_uses_owned_ytdlp_and_preserves_manual_then_auto_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    base_args = ["python", "-m", "yt_dlp", "--no-config", "--proxy", "http://127.0.0.1:10808"]
    monkeypatch.setattr(ffmpeg, "YTDLP_BASE_ARGS", base_args)
    calls: list[tuple[list[str], dict[str, object]]] = []

    async def fake_owned(command, **kwargs):
        argv = list(command)
        calls.append((argv, dict(kwargs)))
        if "--write-auto-subs" in argv:
            (tmp_path / "yt_transcript_fake.en.vtt").write_text(
                "WEBVTT\n\n"
                "00:00:00.000 --> 00:00:02.000\n"
                "hello world\n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(transcript, "run_cancellable_process", fake_owned)

    result = await transcript.download_youtube_transcript_text(
        "https://www.youtube.com/watch?v=example",
        tmp_path,
        lang="en",
    )

    assert result == "[0:00] hello world"
    assert len(calls) == 2

    manual, manual_kwargs = calls[0]
    auto, auto_kwargs = calls[1]
    assert manual[: len(base_args)] == base_args
    assert auto[: len(base_args)] == base_args
    assert "--write-subs" in manual
    assert "--write-auto-subs" not in manual
    assert "--write-auto-subs" in auto
    assert "--write-subs" not in auto
    assert manual_kwargs == {"timeout": 240, "text": True}
    assert auto_kwargs == {"timeout": 240, "text": True}
    assert "--skip-download" in manual
    assert "--sub-format" in manual
    assert "vtt/best" in manual


@pytest.mark.asyncio
async def test_transcript_cancellation_propagates_from_process_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(ffmpeg, "YTDLP_BASE_ARGS", ["python", "-m", "yt_dlp"])

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(transcript, "run_cancellable_process", cancelled)

    with pytest.raises(asyncio.CancelledError):
        await transcript.download_youtube_transcript_text(
            "https://www.youtube.com/watch?v=example",
            tmp_path,
        )


def test_transcript_owner_has_no_executor_or_direct_subprocess_escape_hatch() -> None:
    source = Path("services/youtube_transcript.py").read_text(encoding="utf-8")

    assert "run_cancellable_process" in source
    assert "run_in_executor" not in source
    assert "subprocess.run" not in source
    assert "CREATE_NO_WINDOW" not in source
