from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from handlers import commands
from services import (
    eng_subtitles,
    pdf_generator,
    render_clips_montage,
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
