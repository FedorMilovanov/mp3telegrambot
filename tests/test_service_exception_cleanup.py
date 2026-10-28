from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services import media_delivery_probe, pdf_generator, shorts_factory_source
from services import (
    shorts_factory_capacity,
    shorts_factory_full_video,
    shorts_factory_retry_cache,
    shorts_transcription,
    shorts_video_impl,
    youtube_transcript,
)


def test_wkhtmltopdf_failure_removes_partial_output(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "wkhtmltopdf"
    executable.write_text("", encoding="utf-8")
    output = tmp_path / "partial.pdf"
    output.write_bytes(b"partial")

    class BrokenProcess:
        def communicate(self, timeout: int) -> tuple[bytes, bytes]:
            raise RuntimeError("renderer failed")

    monkeypatch.setattr(pdf_generator._subprocess, "Popen", lambda *args, **kwargs: BrokenProcess())

    assert pdf_generator._gen_wkhtmltopdf("html", str(output), "title", "author", str(executable)) is None
    assert not output.exists()


def test_wkhtmltopdf_timeout_survives_kill_failure(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "wkhtmltopdf"
    executable.write_text("", encoding="utf-8")

    class TimedOutProcess:
        kill_attempted = False

        def communicate(self, timeout: int) -> tuple[bytes, bytes]:
            raise pdf_generator._subprocess.TimeoutExpired("wkhtmltopdf", timeout)

        def kill(self) -> None:
            self.kill_attempted = True
            raise RuntimeError("process handle unavailable")

    process = TimedOutProcess()
    monkeypatch.setattr(pdf_generator._subprocess, "Popen", lambda *args, **kwargs: process)

    result = pdf_generator._gen_wkhtmltopdf(
        "html",
        str(tmp_path / "timeout.pdf"),
        "title",
        "author",
        str(executable),
    )

    assert result is None
    assert process.kill_attempted is True


def test_weasyprint_failure_removes_partial_output(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "partial.pdf"

    class BrokenHTML:
        def __init__(self, *, string: str) -> None:
            self.string = string

        def write_pdf(self, target: str) -> None:
            Path(target).write_bytes(b"partial")
            raise RuntimeError("renderer failed")

    monkeypatch.setattr(pdf_generator, "_HAS_WEASYPRINT", True)
    monkeypatch.setattr(pdf_generator, "WeasyprintHTML", BrokenHTML)

    assert pdf_generator._gen_weasyprint("html", str(output)) is None
    assert not output.exists()


@pytest.mark.asyncio
async def test_factory_capacity_safe_status_delivers_edit() -> None:
    status = SimpleNamespace(edit_text=AsyncMock())
    await shorts_factory_capacity.safe_status(status, "ready")
    status.edit_text.assert_awaited_once_with("ready")


@pytest.mark.asyncio
async def test_factory_full_video_failure_still_reports_error(monkeypatch, tmp_path: Path) -> None:
    video = tmp_path / "translated.mp4"
    video.write_bytes(b"video")
    message = SimpleNamespace(
        reply_video=AsyncMock(side_effect=RuntimeError("upload failed")),
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(effective_message=message, message=message)
    monkeypatch.setattr(
        shorts_factory_full_video,
        "factory_full_video_requested",
        AsyncMock(return_value=True),
    )

    delivered = await shorts_factory_full_video.send_factory_full_translation_if_enabled(
        update,
        video,
        title="Title",
        duration=2,
        translation_required=True,
    )

    assert delivered is False
    message.reply_text.assert_awaited_once()
    assert "upload failed" in message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_retry_cache_tolerates_concurrent_file_creation(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x" * 2048)
    cache_dir = tmp_path / "cache"
    probe = SimpleNamespace(duration=1.0)
    monkeypatch.setattr(media_delivery_probe, "probe_media_async", AsyncMock(return_value=probe))
    monkeypatch.setattr(shorts_factory_source, "factory_audio_probe_is_usable", lambda value: True)
    monkeypatch.setattr(shorts_factory_source, "measure_factory_audio_duration", AsyncMock(return_value=1.0))
    monkeypatch.setattr(shorts_factory_retry_cache, "FACTORY_CACHE_DIR", cache_dir)
    monkeypatch.setattr(shorts_factory_retry_cache, "cleanup_retry_cache", lambda: None)

    def concurrent_copy(source_path: Path, destination: Path) -> None:
        destination.write_bytes(source_path.read_bytes())
        raise FileExistsError("another worker won the race")

    monkeypatch.setattr(shorts_factory_retry_cache, "copy_or_link", concurrent_copy)

    await shorts_factory_retry_cache._store_analysis_audio("https://example.test/video", "media", source)

    stored = list(cache_dir.glob("*.mp4"))
    assert len(stored) == 1
    assert stored[0].read_bytes() == source.read_bytes()


@pytest.mark.asyncio
async def test_short_transcription_cleans_wav_after_extraction_failure(monkeypatch, tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(shorts_transcription._impl, "HAS_FASTER_WHISPER", True)
    monkeypatch.setattr(shorts_transcription.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(
        shorts_transcription,
        "run_cancellable_process",
        AsyncMock(return_value=SimpleNamespace(returncode=1)),
    )

    assert await shorts_transcription.transcribe_short_clip(video) == []


@pytest.mark.asyncio
async def test_short_transcription_uses_shared_cuda_cleanup(monkeypatch, tmp_path: Path) -> None:
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    wav_paths: list[Path] = []
    profile = {
        "model_name": "large-v3",
        "karaoke": False,
        "word_timestamps": False,
        "light": False,
        "gemini_hints": False,
    }
    segment = SimpleNamespace(start=0.0, end=1.0, text=" text ", words=[])
    info = SimpleNamespace(duration=1.0, language="ru", language_probability=0.99)
    model = SimpleNamespace(transcribe=Mock(return_value=([segment], info)))
    clear_cuda = Mock()

    async def fake_ffmpeg(command, timeout: int):
        wav_path = Path(command[-1])
        wav_paths.append(wav_path)
        await asyncio.to_thread(wav_path.write_bytes, b"x" * 2048)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(shorts_transcription._impl, "HAS_FASTER_WHISPER", True)
    monkeypatch.setattr(shorts_transcription._impl, "get_subtitles_mode_settings", lambda: profile)
    monkeypatch.setattr(shorts_transcription._impl, "build_whisper_initial_prompt", lambda *_a, **_k: "prompt")
    monkeypatch.setattr(shorts_transcription._impl, "_clear_torch_cuda_cache", clear_cuda)
    monkeypatch.setattr(shorts_transcription._impl, "_get_whisper_model", lambda _size: model)
    monkeypatch.setattr(shorts_transcription._impl, "_polish_subtitle_text", lambda text: text.strip())
    monkeypatch.setattr(shorts_transcription.shutil, "which", lambda _name: "/ffmpeg")
    monkeypatch.setattr(shorts_transcription, "run_cancellable_process", fake_ffmpeg)

    result = await shorts_transcription.transcribe_short_clip(video)

    assert result == [{"start": 0.0, "end": 1.0, "text": "text", "words": []}]
    clear_cuda.assert_called_once_with()
    model.transcribe.assert_called_once()
    assert wav_paths and not wav_paths[0].exists()


def test_whisper_reset_releases_existing_model(monkeypatch) -> None:
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(shorts_video_impl, "_whisper_model", object())
    monkeypatch.setattr(shorts_video_impl, "_whisper_model_name", "old")

    shorts_video_impl._reset_whisper_model()

    assert shorts_video_impl._whisper_model is None
    assert shorts_video_impl._whisper_model_name is None


def test_whisper_reset_ignores_cuda_cleanup_failure(monkeypatch) -> None:
    def _broken_cuda() -> bool:
        raise RuntimeError("CUDA is unavailable")

    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=_broken_cuda))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(shorts_video_impl, "_whisper_model", object())
    monkeypatch.setattr(shorts_video_impl, "_whisper_model_name", "old")

    shorts_video_impl._reset_whisper_model()

    assert shorts_video_impl._whisper_model is None
    assert shorts_video_impl._whisper_model_name is None


def test_cuda_cache_cleanup_releases_and_synchronizes(monkeypatch) -> None:
    empty_cache = Mock()
    synchronize = Mock()
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(
            is_available=lambda: True,
            empty_cache=empty_cache,
            synchronize=synchronize,
        )
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    shorts_video_impl._clear_torch_cuda_cache()

    empty_cache.assert_called_once_with()
    synchronize.assert_called_once_with()


def test_whisper_model_change_releases_previous_instance(monkeypatch) -> None:
    replacement = object()
    fake_torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=lambda *args, **kwargs: replacement),
    )
    monkeypatch.setenv("WHISPER_FORCE_CPU", "1")
    monkeypatch.setattr(shorts_video_impl, "_whisper_model", object())
    monkeypatch.setattr(shorts_video_impl, "_whisper_model_name", "old")

    assert shorts_video_impl._get_whisper_model("new") is replacement
    assert shorts_video_impl._whisper_model_name == "new"


@pytest.mark.asyncio
async def test_youtube_transcript_removes_stale_vtt_before_download(monkeypatch, tmp_path: Path) -> None:
    stale = tmp_path / "yt_transcript_stale.vtt"
    stale.write_text("stale", encoding="utf-8")
    monkeypatch.setenv("SYNOPSIS_YT_TRANSCRIPT", "1")
    monkeypatch.setattr(
        youtube_transcript,
        "run_cancellable_process",
        AsyncMock(return_value=SimpleNamespace(returncode=1, stdout="", stderr="unavailable")),
    )

    assert await youtube_transcript.download_youtube_transcript_text("https://example.test", tmp_path) == ""
    assert not stale.exists()
