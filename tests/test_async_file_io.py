from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from handlers import commands
from services import (
    eng_subtitles,
    livedub_mix,
    livedub_qa,
    pdf_generator,
    render_clips_montage,
    shorts_candidates,
    shorts_factory_candidates,
    shorts_video,
    yandex_live_dub,
)
from services import translation_editorial_factory as editorial_factory


def _write_file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _path_exists(path: Path) -> bool:
    return path.exists()


def _matching_paths(directory: Path, pattern: str) -> list[Path]:
    return list(directory.glob(pattern))


def _expected_concat(directory: Path) -> str:
    return "".join(f"file '{(directory / f'montage_part{index}.mp4').resolve()}'\n" for index in range(2))


def _update() -> SimpleNamespace:
    message = SimpleNamespace(reply_document=AsyncMock(), reply_text=AsyncMock())
    return SimpleNamespace(effective_user=SimpleNamespace(id=123), message=message)


@pytest.mark.asyncio
async def test_archive_quality_file_success_opens_and_closes_document(monkeypatch, tmp_path: Path) -> None:
    path = _write_file(tmp_path / "quality.md", b"quality")
    update = _update()
    monkeypatch.setattr(commands, "ADMIN_IDS", {123})
    monkeypatch.setattr(
        commands,
        "awrite_archive_quality_exports",
        AsyncMock(return_value={"md": str(path)}),
    )

    await commands.archivequalityfile_command(update, SimpleNamespace(args=[]))

    kwargs = update.message.reply_document.await_args.kwargs
    assert kwargs["filename"] == "quality.md"
    assert kwargs["document"].closed


@pytest.mark.asyncio
async def test_archive_file_success_opens_and_closes_document(monkeypatch, tmp_path: Path) -> None:
    path = _write_file(tmp_path / "latest.md", b"archive")
    update = _update()
    monkeypatch.setattr(commands, "ADMIN_IDS", {123})
    monkeypatch.setattr(commands, "get_archive_export_path", lambda kind: path)

    await commands.archivefile_command(update, SimpleNamespace(args=["latest"]))

    kwargs = update.message.reply_document.await_args.kwargs
    assert kwargs["filename"] == "latest.md"
    assert kwargs["document"].closed


@pytest.mark.asyncio
async def test_segment_file_success_opens_and_closes_document(monkeypatch, tmp_path: Path) -> None:
    path = _write_file(tmp_path / "video_segments.md", b"segments")
    update = _update()
    monkeypatch.setattr(commands, "ADMIN_IDS", {123})
    monkeypatch.setattr(commands, "_resolve_segment_source", AsyncMock(return_value=("video", None, None)))
    monkeypatch.setattr(commands, "get_segment_export_paths", lambda video_id: {"md": path})

    await commands.segmentfile_command(update, SimpleNamespace(args=["last", "md"]))

    kwargs = update.message.reply_document.await_args.kwargs
    assert kwargs["filename"] == "video_segments.md"
    assert kwargs["document"].closed


@pytest.mark.asyncio
async def test_segment_file_rebuilds_missing_export_from_cache(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "video_segments.md"
    update = _update()
    cache = {
        "ai_data": {
            "real_title": "Title",
            "real_author": "Author",
            "timestamps": "00:00 Intro",
            "duration": 42,
            "format": "sermon",
        }
    }
    saved = []
    monkeypatch.setattr(commands, "ADMIN_IDS", {123})
    monkeypatch.setattr(commands, "_resolve_segment_source", AsyncMock(return_value=("video", cache, None)))
    monkeypatch.setattr(commands, "get_segment_export_paths", lambda video_id: {"md": path})

    async def fake_save(**kwargs):
        saved.append(kwargs)
        await asyncio.to_thread(_write_file, path, b"rebuilt")

    monkeypatch.setattr(commands, "asave_segment_plan_export", fake_save)

    await commands.segmentfile_command(update, SimpleNamespace(args=["last", "md"]))

    assert saved == [
        {
            "video_id": "video",
            "title": "Title",
            "author": "Author",
            "timestamps": "00:00 Intro",
            "duration": 42,
            "format_name": "sermon",
        }
    ]
    kwargs = update.message.reply_document.await_args.kwargs
    assert kwargs["filename"] == "video_segments.md"
    assert kwargs["document"].closed


@pytest.mark.asyncio
async def test_pdf_command_success_sends_and_cleans_generated_file(monkeypatch, tmp_path: Path) -> None:
    update = _update()
    status = SimpleNamespace(edit_text=AsyncMock())
    update.message.reply_text = AsyncMock(return_value=status)
    monkeypatch.setattr(commands, "ADMIN_IDS", {123})
    monkeypatch.setattr(commands, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(
        commands,
        "_resolve_segment_source",
        AsyncMock(return_value=("video", {"ai_data": {}, "telegraph_url": "https://example.test/synopsis"}, None)),
    )

    async def fake_generate_pdf(**kwargs) -> str:
        path = Path(kwargs["output_path"])
        await asyncio.to_thread(_write_file, path, b"%PDF" + b"x" * 1024)
        return str(path)

    monkeypatch.setattr(pdf_generator, "generate_sermon_pdf_async", fake_generate_pdf)

    await commands.pdf_command(update, SimpleNamespace(args=["video"]))

    kwargs = update.message.reply_document.await_args.kwargs
    assert kwargs["filename"].endswith(".pdf")
    assert kwargs["document"].closed
    status.edit_text.assert_awaited_with("✅ PDF отправлен.")
    assert _matching_paths(tmp_path, "video_*.pdf") == []


@pytest.mark.asyncio
async def test_pdf_command_rejects_undersized_output_and_cleans_file(monkeypatch, tmp_path: Path) -> None:
    update = _update()
    status = SimpleNamespace(edit_text=AsyncMock())
    update.message.reply_text = AsyncMock(return_value=status)
    monkeypatch.setattr(commands, "ADMIN_IDS", {123})
    monkeypatch.setattr(commands, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(
        commands,
        "_resolve_segment_source",
        AsyncMock(return_value=("video", {"ai_data": {}, "telegraph_url": "https://example.test/synopsis"}, None)),
    )

    async def fake_generate_pdf(**kwargs) -> str:
        path = Path(kwargs["output_path"])
        await asyncio.to_thread(_write_file, path, b"%PDF")
        return str(path)

    monkeypatch.setattr(pdf_generator, "generate_sermon_pdf_async", fake_generate_pdf)

    await commands.pdf_command(update, SimpleNamespace(args=["video"]))

    update.message.reply_document.assert_not_awaited()
    status.edit_text.assert_awaited_with("❌ Не удалось создать PDF.")
    assert _matching_paths(tmp_path, "video_*.pdf") == []


@pytest.mark.asyncio
async def test_status_command_reports_database_size_and_latest_backup(monkeypatch, tmp_path: Path) -> None:
    import shutil

    database = _write_file(tmp_path / "bot.db", b"x" * 4096)
    _write_file(tmp_path / "bot.db.bak.2026-10-26", b"old")
    _write_file(tmp_path / "bot.db.bak.2026-10-27", b"new")
    update = _update()
    monkeypatch.setattr(commands, "ADMIN_IDS", {123})
    monkeypatch.setattr(commands, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(commands, "DB_PATH", str(database))
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=1024**3, total=2 * 1024**3),
    )
    monkeypatch.setattr("services.ffmpeg._supported_js_runtimes", lambda: [])
    monkeypatch.setattr("services.ffmpeg._get_video_encoder", lambda: ("encoder", "", ""))
    monkeypatch.setattr("services.livedub_mix.get_mix_params", lambda: {})
    monkeypatch.setattr("core.database.current_livedub_file_id_cache_fingerprint", lambda: "cache")
    monkeypatch.setattr("services.operator_runtime_status.safe_operator_runtime_status_html_lines", lambda: [])

    await commands.status_command(update, SimpleNamespace())

    text = update.message.reply_text.await_args.args[0]
    assert "🗄 БД: 4 КБ · бэкап: 2026-10-27" in text


@pytest.mark.asyncio
async def test_disk_command_reports_file_count_and_database_size(monkeypatch, tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    _write_file(downloads / "one.mp3", b"one")
    _write_file(downloads / "two.mp4", b"two")
    database = _write_file(tmp_path / "bot.db", b"x" * 2048)
    update = _update()
    monkeypatch.setattr(commands, "ADMIN_IDS", {123})
    monkeypatch.setattr(commands, "DOWNLOAD_DIR", downloads)
    monkeypatch.setattr(commands, "DB_PATH", str(database))

    await commands.disk_command(update, SimpleNamespace())

    text = update.message.reply_text.await_args.args[0]
    assert "<b>Файлов в downloads:</b> 2" in text
    assert "<b>Размер БД:</b> 2 КБ" in text


@pytest.mark.parametrize("stdout_path_exists", [True, False], ids=["stdout", "fallback"])
@pytest.mark.asyncio
async def test_new_protocol_audio_returns_discovered_path(
    monkeypatch, tmp_path: Path, stdout_path_exists: bool
) -> None:
    downloaded = _write_file(tmp_path / "translation.mp3", b"audio")
    reported = downloaded if stdout_path_exists else tmp_path / "missing.mp3"
    monkeypatch.setattr(
        yandex_live_dub,
        "_run_subprocess",
        lambda *_args, **_kwargs: (str(reported), "", 0),
    )

    result = await yandex_live_dub._get_audio_new_protocol(
        ["vot"],
        "https://example.invalid/video",
        tmp_path,
        timeout=1,
        voice_style="live",
    )

    assert result == downloaded


@pytest.mark.parametrize("stdout_path_exists", [True, False], ids=["stdout", "fallback"])
@pytest.mark.asyncio
async def test_legacy_audio_returns_discovered_path(monkeypatch, tmp_path: Path, stdout_path_exists: bool) -> None:
    downloaded = _write_file(tmp_path / "translation.mp3", b"audio")
    reported = downloaded if stdout_path_exists else tmp_path / "missing.mp3"
    monkeypatch.setattr(yandex_live_dub, "_ensure_vot_helper", lambda: None)
    monkeypatch.setattr(yandex_live_dub, "_check_vot_cli", lambda: "vot")
    monkeypatch.setattr(
        yandex_live_dub,
        "_run_subprocess",
        lambda *_args, **_kwargs: (str(reported), "", 0),
    )
    monkeypatch.setattr("services.livedub_ru_provenance.snapshot_ru_audio_candidates", lambda _workdir: None)
    monkeypatch.setattr("services.livedub_ru_provenance.record_returned_ru_audio", lambda *_args, **_kwargs: None)

    result = await yandex_live_dub.get_live_dub_audio(
        "https://example.invalid/video",
        tmp_path,
        retries=0,
    )

    assert result == downloaded


@pytest.mark.parametrize("stdout_path_exists", [True, False], ids=["stdout", "fallback"])
@pytest.mark.asyncio
async def test_legacy_video_returns_discovered_path(monkeypatch, tmp_path: Path, stdout_path_exists: bool) -> None:
    downloaded = _write_file(tmp_path / "translation.mp4", b"video")
    reported = downloaded if stdout_path_exists else tmp_path / "missing.mp4"
    monkeypatch.setattr(
        yandex_live_dub,
        "get_live_dub_audio",
        AsyncMock(side_effect=RuntimeError("force legacy merge")),
    )
    monkeypatch.setattr(yandex_live_dub, "_check_vot_cli", lambda: "vot")
    monkeypatch.setattr(
        yandex_live_dub,
        "_run_subprocess",
        lambda *_args, **_kwargs: (str(reported), "", 0),
    )

    result = await yandex_live_dub.get_live_dub_video("https://example.invalid/video", tmp_path)

    assert result == downloaded


@pytest.mark.parametrize(
    ("payload", "expected"),
    [(b"processed", True), (b"", False)],
    ids=["nonempty-output", "empty-output"],
)
@pytest.mark.asyncio
async def test_normalize_transform_validates_output(
    monkeypatch,
    tmp_path: Path,
    payload: bytes,
    expected: bool,
) -> None:
    input_path = _write_file(tmp_path / "input.mp4", b"input")
    output_path = tmp_path / "processed.mp4"
    commands = []

    async def run(command, **_kwargs):
        commands.append(list(command))
        _write_file(output_path, payload)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(shorts_video._impl.shutil, "which", lambda _name: "ffmpeg")
    monkeypatch.setattr(shorts_video._impl, "run_cancellable_process", run)

    result = await shorts_video.postprocess_short(input_path, output_path)

    assert result is expected
    assert len(commands) == 1
    codec_index = commands[0].index("-c:v")
    assert commands[0][codec_index + 1] == "copy"
    if expected:
        assert _read_bytes(output_path) == payload
    else:
        assert not _path_exists(output_path)


@pytest.mark.parametrize(
    ("public_name", "legacy_name", "extra_args"),
    [
        ("create_short_title_poster", "_LEGACY_CREATE_SHORT_TITLE_POSTER", ("Title", 30.0)),
        ("create_short_snapshot", "_LEGACY_CREATE_SHORT_SNAPSHOT", (30.0,)),
    ],
    ids=["title-poster", "snapshot"],
)
@pytest.mark.parametrize(
    ("payload", "expected"),
    [(b"generated", True), (b"", False)],
    ids=["nonempty-output", "empty-output"],
)
@pytest.mark.asyncio
async def test_optional_short_outputs_validate_generated_file(
    monkeypatch,
    tmp_path: Path,
    public_name: str,
    legacy_name: str,
    extra_args: tuple,
    payload: bytes,
    expected: bool,
) -> None:
    input_path = _write_file(tmp_path / "input.mp4", b"input")
    output_path = tmp_path / "optional.out"

    async def produce(source: Path, destination: Path, *args):
        assert source == input_path
        assert destination == output_path
        assert args == extra_args
        _write_file(destination, payload)
        return True

    monkeypatch.setattr(shorts_video, legacy_name, produce)
    produce_public = getattr(shorts_video, public_name)

    result = await produce_public(input_path, output_path, *extra_args)

    assert result is expected
    if expected:
        assert _read_bytes(output_path) == payload
    else:
        assert not _path_exists(output_path)


@pytest.mark.asyncio
async def test_translation_qa_cleans_temporary_original_and_qa_audio(monkeypatch, tmp_path: Path) -> None:
    dub_video = _write_file(tmp_path / "dub.mp4", b"video")
    original = _write_file(tmp_path / "original.m4a", b"original")
    dub_audio = _write_file(tmp_path / "clean_ru.mp3", b"dub")
    qa_audio = _write_file(tmp_path / "dub_qa.mp3", b"qa")
    temporary_original = tmp_path / "original_qa_original.mp3"
    client = object()
    upload = AsyncMock(side_effect=RuntimeError("stop after extraction"))

    class QuotaTracker:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def should_skip(self, *_args, **_kwargs) -> bool:
            return False

        def record_project_scoped_error(self, *_args, **_kwargs) -> None:
            pass

    def extract(source: Path, destination: Path) -> Path:
        assert source == original
        assert destination == temporary_original
        destination.write_bytes(b"temporary")
        return destination

    monkeypatch.setattr(livedub_qa, "HAS_GEMINI", True)
    monkeypatch.setattr(livedub_qa, "GEMINI_CLIENTS", [client])
    monkeypatch.setattr(livedub_qa, "types", SimpleNamespace())
    monkeypatch.setattr(livedub_qa.capacity_control, "require_domain_available", lambda _domain: None)
    monkeypatch.setattr(livedub_qa, "ProjectQuotaDomainTracker", QuotaTracker)
    monkeypatch.setattr(livedub_qa, "_extract_audio_for_qa", extract)
    monkeypatch.setattr(livedub_qa, "_upload_and_wait", upload)

    result = await livedub_qa._run_translation_qa_base(
        dub_video,
        original,
        {"main_topic": "Topic"},
        10,
        model_name="model",
        dub_audio_path=dub_audio,
    )

    assert result is None
    assert upload.await_count == 1
    assert not temporary_original.exists()
    assert not qa_audio.exists()
    assert original.exists()
    assert dub_audio.exists()


def _patch_qa_audio_fix_runtime(
    monkeypatch,
    original: Path,
    translated: Path,
    fixed: Path,
    *,
    actual_duration: float = 10.0,
) -> None:
    monkeypatch.setattr(livedub_mix, "extract_fix_intervals", lambda _issues: [(1.0, 2.0)])
    monkeypatch.setattr(livedub_mix, "find_pro_tracks", lambda _workdir: (original, translated))
    monkeypatch.setattr(livedub_mix, "mix_tracks", AsyncMock(return_value=fixed))
    monkeypatch.setattr(livedub_mix, "has_video_stream", lambda _path: True)
    monkeypatch.setattr(
        livedub_mix,
        "probe_media_duration",
        lambda path: actual_duration if Path(path) == fixed else 10.0,
    )


@pytest.mark.asyncio
async def test_qa_audio_fix_skips_when_clean_tracks_are_missing(monkeypatch, tmp_path: Path) -> None:
    mix = AsyncMock()
    monkeypatch.setattr(livedub_mix, "extract_fix_intervals", lambda _issues: [(1.0, 2.0)])
    monkeypatch.setattr(livedub_mix, "find_pro_tracks", lambda _workdir: (None, None))
    monkeypatch.setattr(livedub_mix, "mix_tracks", mix)

    result = await livedub_mix.apply_qa_audio_fixes(tmp_path, [{"time": "1:00", "severity": "major"}])

    assert result is None
    mix.assert_not_called()


@pytest.mark.asyncio
async def test_qa_audio_fix_deletes_output_that_fails_duration_postcondition(monkeypatch, tmp_path: Path) -> None:
    original = _write_file(tmp_path / "original.mp4", b"video")
    translated = _write_file(tmp_path / "translation.mp3", b"audio")
    fixed = _write_file(tmp_path / "pro_dub_fixed.mp4", b"broken")
    _patch_qa_audio_fix_runtime(monkeypatch, original, translated, fixed, actual_duration=20.0)

    with pytest.raises(RuntimeError, match="QA auto-fix postcondition failed"):
        await livedub_mix.apply_qa_audio_fixes(tmp_path, [{"time": "1:00", "severity": "major"}])

    assert not fixed.exists()


@pytest.mark.asyncio
async def test_qa_audio_fix_returns_fixed_video_without_subtitles(monkeypatch, tmp_path: Path) -> None:
    original = _write_file(tmp_path / "original.mp4", b"video")
    translated = _write_file(tmp_path / "translation.mp3", b"audio")
    fixed = _write_file(tmp_path / "pro_dub_fixed.mp4", b"fixed")
    _patch_qa_audio_fix_runtime(monkeypatch, original, translated, fixed)

    result = await livedub_mix.apply_qa_audio_fixes(tmp_path, [{"time": "1:00", "severity": "major"}])

    assert result == fixed


@pytest.mark.asyncio
async def test_qa_audio_fix_restores_valid_subtitles(monkeypatch, tmp_path: Path) -> None:
    original = _write_file(tmp_path / "original.mp4", b"video")
    translated = _write_file(tmp_path / "translation.mp3", b"audio")
    fixed = _write_file(tmp_path / "pro_dub_fixed.mp4", b"fixed")
    subtitles = _write_file(tmp_path / "gemini_subs.srt", b"x" * 60)
    with_subtitles = _write_file(tmp_path / "pro_dub_fixed_subs.mp4", b"fixed-subtitles")
    merge = AsyncMock(return_value=with_subtitles)
    _patch_qa_audio_fix_runtime(monkeypatch, original, translated, fixed)
    monkeypatch.setattr(eng_subtitles, "merge_subtitles", merge)

    result = await livedub_mix.apply_qa_audio_fixes(tmp_path, [{"time": "1:00", "severity": "major"}])

    assert result == with_subtitles
    assert merge.await_args.args == (fixed, subtitles)
    assert merge.await_args.kwargs == {"is_fallback": False}


@pytest.mark.asyncio
async def test_mix_tracks_rejects_audio_only_original(monkeypatch, tmp_path: Path) -> None:
    run_ffmpeg = Mock()
    monkeypatch.setattr(livedub_mix, "has_video_stream", lambda _path: False)
    monkeypatch.setattr(livedub_mix, "_run_ffmpeg", run_ffmpeg)

    result = await livedub_mix.mix_tracks(
        tmp_path / "original.m4a",
        tmp_path / "translation.mp3",
        tmp_path / "mixed.mp4",
    )

    assert result is None
    run_ffmpeg.assert_not_called()


@pytest.mark.parametrize("output", [b"x" * 10241, b"x", None], ids=["ready", "undersized", "missing"])
@pytest.mark.asyncio
async def test_mix_tracks_validates_generated_output(monkeypatch, tmp_path: Path, output: bytes | None) -> None:
    original = _write_file(tmp_path / "original.mp4", b"video")
    translated = _write_file(tmp_path / "translation.mp3", b"audio")
    output_path = tmp_path / "mixed.mp4"
    calls: list[list[str]] = []
    monkeypatch.setattr(livedub_mix, "has_video_stream", lambda _path: True)
    monkeypatch.setattr(livedub_mix, "measure_loudness", lambda _path: -20.0)
    monkeypatch.setattr(livedub_mix, "probe_media_duration", lambda _path: 10.0)
    monkeypatch.setattr(
        livedub_mix,
        "get_mix_params",
        lambda: {
            "orig_volume": 0.45,
            "trans_volume": 1.3,
            "delay_ms": 0,
            "tail_margin_ms": 0,
            "tail_freeze_max_sec": 0,
            "voice_eq": False,
            "rnnoise_model": "",
        },
    )

    def run_ffmpeg(args: list[str]):
        calls.append(args)
        if output is not None:
            output_path.write_bytes(output)
        return True, ""

    monkeypatch.setattr(livedub_mix, "_run_ffmpeg", run_ffmpeg)

    result = await livedub_mix.mix_tracks(original, translated, output_path)

    assert (result == output_path) if output is not None and len(output) > 10240 else (result is None)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_shorts_candidates_reads_inline_audio_and_validates_response(monkeypatch, tmp_path: Path) -> None:
    payload = b"x" * 2048
    audio = _write_file(tmp_path / "shorts.mp3", payload)
    client = object()
    from_bytes = Mock(return_value="audio-part")
    response = SimpleNamespace(
        text=(
            '{"shorts_candidates": [{"start": "00:00", "end": "00:40", '
            '"title": "Candidate", "reason": "Reason", "kind": "story", "hashtags": []}]}'
        )
    )
    generate_content = AsyncMock(return_value=response)

    async def generate(clients, callback):
        return await callback(clients[0])

    monkeypatch.setattr(shorts_candidates, "HAS_GEMINI", True)
    monkeypatch.setattr(shorts_candidates, "GEMINI_CLIENTS", [client])
    monkeypatch.setattr(
        shorts_candidates,
        "types",
        SimpleNamespace(Part=SimpleNamespace(from_bytes=from_bytes)),
    )
    monkeypatch.setattr(shorts_candidates, "gemini_generate", generate)
    monkeypatch.setattr(shorts_candidates, "_generate_audio_candidate_content", generate_content)
    monkeypatch.setattr(shorts_candidates, "alog_gemini_response", AsyncMock())
    monkeypatch.setattr(shorts_candidates, "alog_gemini_run", AsyncMock())

    result = await shorts_candidates.create_shorts_candidates(
        audio,
        {},
        "Title",
        "Author",
        duration=120,
    )

    assert from_bytes.call_args.kwargs == {"data": payload, "mime_type": "audio/mpeg"}
    assert generate_content.await_args.kwargs["contents"][0] == "audio-part"
    assert len(result) == 1
    assert result[0]["start_seconds"] == 0
    assert result[0]["end_seconds"] == 40


@pytest.mark.asyncio
async def test_clips_candidates_reads_inline_audio_and_validates_response(monkeypatch, tmp_path: Path) -> None:
    payload = b"x" * 2048
    audio = _write_file(tmp_path / "clips.mp3", payload)
    client = object()
    from_bytes = Mock(return_value="audio-part")
    response = SimpleNamespace(
        text=(
            '{"clip_candidates": [{"start": "00:00", "end": "06:00", '
            '"title": "Candidate", "reason": "Reason", "kind": "interview", "hashtags": []}]}'
        )
    )
    generate_content = AsyncMock(return_value=response)

    async def generate(clients, callback):
        return await callback(clients[0])

    monkeypatch.setattr(shorts_candidates, "HAS_GEMINI", True)
    monkeypatch.setattr(shorts_candidates, "GEMINI_CLIENTS", [client])
    monkeypatch.setattr(
        shorts_candidates,
        "types",
        SimpleNamespace(Part=SimpleNamespace(from_bytes=from_bytes)),
    )
    monkeypatch.setattr(shorts_candidates, "gemini_generate", generate)
    monkeypatch.setattr(shorts_candidates, "_generate_audio_candidate_content", generate_content)
    monkeypatch.setattr(shorts_candidates, "alog_gemini_response", AsyncMock())
    monkeypatch.setattr(shorts_candidates, "alog_gemini_run", AsyncMock())

    result = await shorts_candidates.create_clips_candidates(
        audio,
        {},
        "Title",
        "Author",
        duration=900,
    )

    assert from_bytes.call_args.kwargs == {"data": payload, "mime_type": "audio/mpeg"}
    assert generate_content.await_args.kwargs["contents"][0] == "audio-part"
    assert len(result) == 1
    assert result[0]["start_seconds"] == 0
    assert result[0]["end_seconds"] == 360


@pytest.mark.asyncio
async def test_factory_plan_rejects_undersized_audio(monkeypatch, tmp_path: Path) -> None:
    audio = _write_file(tmp_path / "small.mp3", b"tiny")
    monkeypatch.setattr(shorts_factory_candidates, "HAS_GEMINI", True)
    monkeypatch.setattr(shorts_factory_candidates, "GEMINI_CLIENTS", [object()])
    monkeypatch.setattr(shorts_factory_candidates, "types", SimpleNamespace())

    with pytest.raises(RuntimeError, match="missing or empty"):
        await shorts_factory_candidates.create_factory_plan(
            audio,
            title="Title",
            performer="Author",
            duration=120,
        )


@pytest.mark.asyncio
async def test_factory_plan_reads_inline_audio_and_returns_validated_plan(monkeypatch, tmp_path: Path) -> None:
    payload = b"x" * 2048
    audio = _write_file(tmp_path / "audio.mp3", payload)
    from_bytes = Mock(return_value="audio-part")
    run_pass = AsyncMock(side_effect=["scout", "judge", "audited"])

    class QuotaTracker:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def should_skip(self, *_args, **_kwargs) -> bool:
            return False

        def record_project_scoped_error(self, *_args, **_kwargs) -> None:
            pass

    monkeypatch.setattr(shorts_factory_candidates, "HAS_GEMINI", True)
    monkeypatch.setattr(shorts_factory_candidates, "GEMINI_CLIENTS", [object()])
    monkeypatch.setattr(
        shorts_factory_candidates,
        "types",
        SimpleNamespace(Part=SimpleNamespace(from_bytes=from_bytes)),
    )
    monkeypatch.setattr(shorts_factory_candidates, "shorts_factory_model", lambda: "factory-model")
    monkeypatch.setattr(shorts_factory_candidates, "_run_pass", run_pass)
    monkeypatch.setattr(
        shorts_factory_candidates,
        "validate_factory_plan",
        lambda *_args, **_kwargs: {"shorts_candidates": [{"start": 1}], "long_candidates": []},
    )
    monkeypatch.setattr("services.gemini_quota_domains.ProjectQuotaDomainTracker", QuotaTracker)

    plan = await shorts_factory_candidates.create_factory_plan(
        audio,
        title="Title",
        performer="Author",
        duration=120,
    )

    assert from_bytes.call_args.kwargs == {"data": payload, "mime_type": "audio/mpeg"}
    assert run_pass.await_count == 3
    assert plan["model"] == "factory-model"
    assert plan["review_passes"] == 3
    assert plan["strict_quality"] is True


@pytest.mark.asyncio
async def test_eng_subtitles_writes_expected_srt_in_worker_thread(monkeypatch, tmp_path: Path) -> None:
    _write_file(tmp_path / "original_audio.m4a", b"audio")
    segment = SimpleNamespace(start=0.0, end=1.5, text="Hello\nworld")
    model = SimpleNamespace(transcribe=Mock(return_value=([segment], None)))
    monkeypatch.setattr(
        eng_subtitles,
        "run_cancellable_process",
        AsyncMock(return_value=SimpleNamespace(returncode=0, stderr="")),
    )
    monkeypatch.setattr(eng_subtitles, "_get_audio_duration", AsyncMock(return_value=1.5))
    monkeypatch.setattr(eng_subtitles, "GEMINI_CLIENTS", [])
    monkeypatch.setattr(shorts_video, "_get_whisper_model", lambda: model)

    result = await eng_subtitles.create_gemini_subtitles(
        "https://example.test/video",
        tmp_path,
        known_duration=2,
        lang="en",
    )

    assert result == tmp_path / "gemini_subs.srt"
    assert _read_text(result) == "1\n00:00:00,000 --> 00:00:01,500\nHello world\n\n"
    assert model.transcribe.call_args.kwargs["language"] == "en"


@pytest.mark.asyncio
async def test_editorial_files_success_sends_all_documents(tmp_path: Path) -> None:
    pack = _write_file(tmp_path / "pack.zip", b"pack")
    review = _write_file(tmp_path / "review.json", b"review")
    markdown = _write_file(tmp_path / "review.md", b"markdown")
    message = SimpleNamespace(reply_document=AsyncMock())

    await editorial_factory.send_factory_editorial_files(
        SimpleNamespace(message=message),
        pack_path=pack,
        review_path=review,
        markdown_path=markdown,
    )

    calls = message.reply_document.await_args_list
    assert [call.kwargs["filename"] for call in calls] == ["pack.zip", "review.json", "review.md"]
    assert all(call.kwargs["document"].closed for call in calls)


@pytest.mark.asyncio
async def test_montage_writes_expected_concat_list_in_worker_thread(monkeypatch, tmp_path: Path) -> None:
    source = _write_file(tmp_path / "source.mp4", b"source")
    output = tmp_path / "montage.mp4"
    concat_contents: list[str] = []
    monkeypatch.setattr(render_clips_montage.shutil, "which", lambda name: "/ffmpeg")
    monkeypatch.setattr(render_clips_montage, "_get_video_encoder", lambda: ("libx264", [], []))

    async def fake_run(command, **_kwargs):
        target = Path(command[-1])
        await asyncio.to_thread(_write_file, target, b"video")
        if "concat" in command:
            concat_path = Path(command[command.index("-i") + 1])
            concat_contents.append(await asyncio.to_thread(_read_text, concat_path))
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(render_clips_montage, "run_cancellable_process", fake_run)

    rendered = await render_clips_montage.render_montage_short(
        source,
        output,
        [
            {"start_seconds": 0.0, "end_seconds": 1.0},
            {"start_seconds": 2.0, "end_seconds": 4.0},
        ],
    )

    expected = _expected_concat(tmp_path)
    assert rendered is True
    assert concat_contents == [expected]
