from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services import eng_subtitles, shorts_video_impl


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _write_jpeg(path: Path) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), (120, 80, 40)).save(path, "JPEG")


def _write_download_candidates(directory: Path, media_id: str) -> None:
    _write_bytes(directory / f"{media_id}_video.webm", b"webm")
    _write_bytes(directory / f"{media_id}_video.mp4", b"mp4")


def _patch_render_runtime(monkeypatch, run) -> None:
    async def no_snap(path, target, search_window=5.0):
        return target

    async def no_crop(path, start_seconds=0.0):
        return ""

    monkeypatch.setattr(shorts_video_impl.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(shorts_video_impl, "_find_silence_end", no_snap)
    monkeypatch.setattr(shorts_video_impl, "_detect_black_bars", no_crop)
    monkeypatch.setattr(
        shorts_video_impl,
        "_get_video_encoder",
        lambda: ("libx264", ["-crf", "23"], ["-preset", "veryfast"]),
    )
    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", run)


@pytest.mark.asyncio
async def test_shorts_download_reuses_verified_workdir_video(monkeypatch, tmp_path: Path) -> None:
    workdir = tmp_path / "workdir"
    download_dir = tmp_path / "downloads"
    audio = workdir / "original_video.f140.m4a"
    video = workdir / "original_video.mp4"
    _write_bytes(audio, b"audio")
    _write_bytes(video, b"video")
    run = AsyncMock()
    monkeypatch.setattr(shorts_video_impl, "DOWNLOAD_DIR", download_dir)
    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", run)
    monkeypatch.setattr(eng_subtitles, "_has_video_stream", lambda path: path.suffix != ".m4a")

    result = await shorts_video_impl._unowned_download_video_for_shorts(
        "https://example.test/video",
        "media",
        workdir,
    )

    assert result == video
    run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("workdir_kind", ["missing", "rejected"])
async def test_shorts_download_falls_back_when_workdir_has_no_reusable_video(
    monkeypatch,
    tmp_path: Path,
    workdir_kind: str,
) -> None:
    workdir = tmp_path / "workdir"
    download_dir = tmp_path / "downloads"
    if workdir_kind == "rejected":
        _write_bytes(workdir / "original_video.part", b"partial")
        _write_bytes(workdir / "original_video.m4a", b"audio")
    monkeypatch.setattr(shorts_video_impl, "DOWNLOAD_DIR", download_dir)
    monkeypatch.setattr(eng_subtitles, "_has_video_stream", lambda path: False)

    async def fake_run(command, **kwargs):
        await asyncio.to_thread(_write_download_candidates, download_dir, "media")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", fake_run)

    result = await shorts_video_impl._unowned_download_video_for_shorts(
        "https://example.test/video",
        "media",
        workdir,
    )

    assert result == download_dir / "media_video.mp4"


@pytest.mark.asyncio
async def test_shorts_download_discovers_preferred_video_after_process(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(shorts_video_impl, "DOWNLOAD_DIR", tmp_path)

    async def fake_run(command, **kwargs):
        await asyncio.to_thread(_write_download_candidates, tmp_path, "media")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", fake_run)

    result = await shorts_video_impl._unowned_download_video_for_shorts("https://example.test/video", "media")

    assert result == tmp_path / "media_video.mp4"
    assert result is not None and result.read_bytes() == b"mp4"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (None, False),
        (b"", False),
        (b"x" * 10_239, False),
        (b"x" * 10_240, True),
    ],
    ids=["missing", "empty", "below-gate", "at-gate"],
)
async def test_legacy_short_render_uses_single_nonempty_size_gate(
    monkeypatch,
    tmp_path: Path,
    payload: bytes | None,
    expected: bool,
) -> None:
    source_path = tmp_path / "source.mp4"
    output_path = tmp_path / "short.mp4"
    _write_bytes(source_path, b"source")

    async def fake_run(command, **kwargs):
        if payload is not None:
            await asyncio.to_thread(_write_bytes, output_path, payload)
        return SimpleNamespace(returncode=0, stderr="")

    _patch_render_runtime(monkeypatch, fake_run)

    result = await shorts_video_impl._unowned_render_short_clip(source_path, output_path, 10, 40)

    assert result is expected
    if expected:
        assert output_path.read_bytes() == payload
    elif payload:
        assert not output_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [(None, False), (b"", False), (b"processed", True)],
    ids=["missing", "empty", "nonempty"],
)
async def test_legacy_short_transform_validates_nonempty_output(
    monkeypatch,
    tmp_path: Path,
    payload: bytes | None,
    expected: bool,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "processed.mp4"
    _write_bytes(input_path, b"input")

    async def fake_run(command, **kwargs):
        if payload is not None:
            await asyncio.to_thread(_write_bytes, output_path, payload)
        return SimpleNamespace(returncode=0, stderr="")

    _patch_render_runtime(monkeypatch, fake_run)

    result = await shorts_video_impl._unowned_short_transform(
        input_path,
        output_path,
        normalize_audio=False,
        speed=1.5,
    )

    assert result is expected
    if expected:
        assert output_path.read_bytes() == payload


@pytest.mark.asyncio
async def test_legacy_short_noop_transform_copies_in_owned_worker(monkeypatch, tmp_path: Path) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "processed.mp4"
    _write_bytes(input_path, b"input")
    run = AsyncMock()
    monkeypatch.setattr(shorts_video_impl.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", run)

    result = await shorts_video_impl._unowned_short_transform(
        input_path,
        output_path,
        normalize_audio=False,
        speed=1.0,
    )

    assert result is True
    assert output_path.read_bytes() == b"input"
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_short_transform_missing_input_does_not_start_process(monkeypatch, tmp_path: Path) -> None:
    run = AsyncMock()
    monkeypatch.setattr(shorts_video_impl.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", run)

    result = await shorts_video_impl._unowned_short_transform(
        tmp_path / "missing.mp4",
        tmp_path / "processed.mp4",
        normalize_audio=False,
        speed=1.5,
    )

    assert result is False
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_short_transcribe_uses_russian_whisper_and_cleans_wav(monkeypatch, tmp_path: Path) -> None:
    video_path = tmp_path / "short.mp4"
    _write_bytes(video_path, b"video")
    wav_paths: list[Path] = []
    segment = SimpleNamespace(
        start=0.0,
        end=0.5,
        text=" Привет ",
        words=[SimpleNamespace(word=" Привет ", start=0.0, end=0.5)],
    )
    info = SimpleNamespace(duration=0.5, language="ru", language_probability=0.99)
    model = SimpleNamespace(transcribe=Mock(return_value=([segment], info)))

    async def fake_run(command, **kwargs):
        wav_path = Path(command[-1])
        wav_paths.append(wav_path)
        await asyncio.to_thread(_write_bytes, wav_path, b"w" * 2048)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(shorts_video_impl, "HAS_FASTER_WHISPER", True)
    monkeypatch.setattr(shorts_video_impl.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", fake_run)
    monkeypatch.setattr(shorts_video_impl, "_get_whisper_model", lambda name: model)
    monkeypatch.setattr(shorts_video_impl, "build_whisper_initial_prompt", lambda *args, **kwargs: "prompt")
    monkeypatch.setattr(
        shorts_video_impl,
        "get_subtitles_mode_settings",
        lambda: {
            "model_name": "large-v3",
            "word_timestamps": True,
            "karaoke": True,
            "light": False,
            "gemini_hints": True,
        },
    )

    result = await shorts_video_impl._unowned_transcribe_short_clip(video_path, {"title": "Test"})

    assert result == [
        {
            "start": 0.0,
            "end": 0.5,
            "text": "Привет",
            "words": [{"word": "Привет", "start": 0.0, "end": 0.5}],
        }
    ]
    assert model.transcribe.call_args.kwargs["language"] == "ru"
    assert model.transcribe.call_args.kwargs["word_timestamps"] is True
    assert len(wav_paths) == 1
    assert not wav_paths[0].exists()


@pytest.mark.asyncio
async def test_legacy_short_transcribe_missing_video_does_not_start_process(monkeypatch, tmp_path: Path) -> None:
    run = AsyncMock()
    monkeypatch.setattr(shorts_video_impl, "HAS_FASTER_WHISPER", True)
    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", run)

    result = await shorts_video_impl._unowned_transcribe_short_clip(tmp_path / "missing.mp4")

    assert result == []
    run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [(None, False), (b"", False), (b"burned", True)],
    ids=["missing", "empty", "nonempty"],
)
async def test_legacy_short_burn_validates_output_and_cleans_ass(
    monkeypatch,
    tmp_path: Path,
    payload: bytes | None,
    expected: bool,
) -> None:
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "burned.mp4"
    _write_bytes(input_path, b"input")
    ass_paths: list[Path] = []

    async def fake_run(command, **kwargs):
        subtitle_filter = command[command.index("-vf") + 1]
        ass_paths.append(Path(subtitle_filter.split("'", 2)[1]))
        if payload is not None:
            await asyncio.to_thread(_write_bytes, output_path, payload)
        return SimpleNamespace(returncode=0, stderr="")

    _patch_render_runtime(monkeypatch, fake_run)
    monkeypatch.setattr(shorts_video_impl, "get_subtitles_mode_settings", lambda: {"karaoke": False})

    result = await shorts_video_impl._unowned_burn_subtitles_into_short(
        input_path,
        output_path,
        [{"start": 0.0, "end": 1.0, "text": "Hello", "words": []}],
    )

    assert result is expected
    assert len(ass_paths) == 1
    assert not ass_paths[0].exists()
    if expected:
        assert output_path.read_bytes() == payload


@pytest.mark.asyncio
async def test_legacy_short_burn_missing_input_does_not_start_process(monkeypatch, tmp_path: Path) -> None:
    run = AsyncMock()
    monkeypatch.setattr(shorts_video_impl.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", run)

    result = await shorts_video_impl._unowned_burn_subtitles_into_short(
        tmp_path / "missing.mp4",
        tmp_path / "burned.mp4",
        [{"start": 0.0, "end": 1.0, "text": "Hello", "words": []}],
    )

    assert result is False
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_short_title_poster_draws_jpeg_and_cleans_frame(monkeypatch, tmp_path: Path) -> None:
    video_path = tmp_path / "short.mp4"
    poster_path = tmp_path / "poster.jpg"
    _write_bytes(video_path, b"video")
    frame_paths: list[Path] = []

    async def fake_run(command, **kwargs):
        frame_path = Path(command[-1])
        frame_paths.append(frame_path)
        await asyncio.to_thread(_write_jpeg, frame_path)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(shorts_video_impl, "HAS_PILLOW", True)
    monkeypatch.setattr(shorts_video_impl.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", fake_run)

    result = await shorts_video_impl._unowned_create_short_title_poster(
        video_path,
        poster_path,
        "Test title",
        30,
    )

    assert result is True
    assert poster_path.read_bytes().startswith(b"\xff\xd8")
    assert len(frame_paths) == 1
    assert not frame_paths[0].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [(None, False), (b"", False), (b"snapshot", True)],
    ids=["missing", "empty", "nonempty"],
)
async def test_legacy_short_snapshot_validates_nonempty_output(
    monkeypatch,
    tmp_path: Path,
    payload: bytes | None,
    expected: bool,
) -> None:
    video_path = tmp_path / "short.mp4"
    snapshot_path = tmp_path / "snapshot.jpg"
    _write_bytes(video_path, b"video")

    async def fake_run(command, **kwargs):
        if payload is not None:
            await asyncio.to_thread(_write_bytes, snapshot_path, payload)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(shorts_video_impl.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(shorts_video_impl, "run_cancellable_process", fake_run)

    result = await shorts_video_impl._unowned_create_short_snapshot(video_path, snapshot_path, 30)

    assert result is expected
    if expected:
        assert snapshot_path.read_bytes() == payload
