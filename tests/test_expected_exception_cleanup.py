from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core import utils as core_utils
from handlers import callbacks
from pipelines import factory_short_delivery, shorts_factory
from pipelines import shorts as shorts_pipeline


def _glob_paths(directory: Path, pattern: str) -> list[Path]:
    return list(directory.glob(pattern))


@pytest.mark.asyncio
async def test_segment_lock_release_failure_does_not_mask_render_result(monkeypatch) -> None:
    from core import render_locks

    release = AsyncMock(side_effect=RuntimeError("lock store unavailable"))
    monkeypatch.setattr(render_locks, "release_render_lock", release)

    await callbacks._release_render_lock("render-token")

    release.assert_awaited_once_with("render-token")


@pytest.mark.asyncio
async def test_nosub_invalid_candidate_json_still_sends_saved_video(monkeypatch, tmp_path: Path) -> None:
    video = tmp_path / "saved_nosub.mp4"
    video.write_bytes(b"video")
    query = SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(reply_video=AsyncMock(), reply_text=AsyncMock()),
    )
    monkeypatch.setattr(callbacks, "DOWNLOAD_DIR", tmp_path)

    await callbacks._handle_strim_nosub(
        query,
        {"video_path_nosub": str(video), "candidate_json": "not-json"},
        0,
        10,
    )

    query.message.reply_video.assert_awaited_once()
    query.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_nosub_missing_file_has_expiry_response(tmp_path: Path) -> None:
    query = SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(reply_video=AsyncMock()),
    )

    await callbacks._handle_strim_nosub(
        query,
        {"video_path_nosub": str(tmp_path / "missing.mp4")},
        0,
        10,
    )

    query.answer.assert_awaited_once_with(
        "⏳ Файл без субтитров истёк — пришлите видео заново.",
        show_alert=True,
    )
    query.message.reply_video.assert_not_awaited()


@pytest.mark.asyncio
async def test_retrim_missing_source_without_url_warns_before_render(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "missing-source.mp4"
    monkeypatch.setattr(
        callbacks,
        "short_trim_get",
        lambda _short_id: {
            "video_path": str(source),
            "start_seconds": 20,
            "end_seconds": 40,
            "source_duration": 60,
            "candidate_json": "{}",
            "yt_url": "",
        },
    )
    retrim = AsyncMock()
    monkeypatch.setattr(callbacks, "_handle_strim_retrim", retrim)
    query = SimpleNamespace(
        data="strim:s10:short-id",
        answer=AsyncMock(),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )

    await callbacks.handle_callback(SimpleNamespace(callback_query=query), SimpleNamespace())

    query.answer.assert_awaited_once_with("✂️ Начало расширено -10 сек, перерезаю...")
    query.message.reply_text.assert_awaited_once_with(
        "⚠️ Исходное видео удалено и нет ссылки для повторного скачивания."
    )
    retrim.assert_not_awaited()


@pytest.mark.asyncio
async def test_retrim_invalid_json_and_render_failure_still_clean_up(monkeypatch, tmp_path: Path) -> None:
    async def fail_render(**_kwargs):
        raise RuntimeError("synthetic render failure")

    monkeypatch.setattr(callbacks, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(callbacks, "render_short_clip", fail_render)
    query = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))

    await callbacks._handle_strim_retrim(
        query,
        {"candidate_json": "not-json"},
        tmp_path / "source.mp4",
        0,
        10,
        "Trim",
        30,
    )

    query.message.reply_text.assert_awaited_once()
    remaining_trims = await asyncio.to_thread(_glob_paths, tmp_path, "*_trim.mp4")
    assert not remaining_trims


@pytest.mark.asyncio
@pytest.mark.parametrize("nosub_available", [False, True])
async def test_retrim_success_preserves_source_and_conditional_nosub_button(
    monkeypatch,
    tmp_path: Path,
    nosub_available: bool,
) -> None:
    source = tmp_path / "source.mp4"
    nosub = tmp_path / "nosub.mp4"
    source.write_bytes(b"source")
    if nosub_available:
        nosub.write_bytes(b"nosub")
    monkeypatch.setattr(callbacks, "DOWNLOAD_DIR", tmp_path)
    rendered = []
    saved = []

    async def fake_render(**kwargs):
        output = kwargs["output_path"]
        output.write_bytes(b"rendered")
        rendered.append(output)
        return True

    monkeypatch.setattr(callbacks, "render_short_clip", fake_render)
    monkeypatch.setattr(callbacks, "short_trim_save", lambda **kwargs: saved.append(kwargs))
    monkeypatch.setattr(callbacks, "build_short_caption", lambda **_kwargs: "caption")
    query = SimpleNamespace(message=SimpleNamespace(reply_video=AsyncMock(), reply_text=AsyncMock()))

    await callbacks._handle_strim_retrim(
        query,
        {"candidate_json": "{}", "video_path_nosub": str(nosub)},
        source,
        0,
        10,
        "Trim",
        30,
    )

    query.message.reply_video.assert_awaited_once()
    query.message.reply_text.assert_not_awaited()
    assert saved[0]["video_path"] == str(source)
    assert len(rendered) == 1
    assert not rendered[0].exists()
    reply_markup = query.message.reply_video.await_args.kwargs["reply_markup"]
    buttons = [button for row in reply_markup.inline_keyboard for button in row]
    nosub_buttons = [button for button in buttons if button.callback_data.startswith("strim:nosub:")]
    assert len(nosub_buttons) == int(nosub_available)


def test_cleanup_files_ignores_locked_media(monkeypatch, tmp_path: Path) -> None:
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    locked = download_dir / "media-video.mp4"
    locked.write_bytes(b"video")
    monkeypatch.setattr(core_utils, "DOWNLOAD_DIR", download_dir)
    monkeypatch.setattr(core_utils, "_THUMBS_LAST_CLEANUP", core_utils.time.time() + 10)
    real_unlink = Path.unlink

    def locked_unlink(self, *args, **kwargs):
        if self == locked:
            raise OSError("locked by ffmpeg")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    core_utils.cleanup_files("media", keep_mp3=False)
    assert locked.read_bytes() == b"video"


@pytest.mark.parametrize("remover", [shorts_pipeline._unlink, factory_short_delivery._remove])
def test_pipeline_unlink_helpers_accept_missing_paths(remover, tmp_path: Path) -> None:
    remover(tmp_path / "already-removed.mp4")


@pytest.mark.asyncio
async def test_factory_safe_status_delivers_edit() -> None:
    status = SimpleNamespace(edit_text=AsyncMock())
    await shorts_factory._safe_status(status, "ready")
    status.edit_text.assert_awaited_once_with("ready")


def test_factory_source_persistence_replaces_stale_variant(monkeypatch, tmp_path: Path) -> None:
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    stale = download_dir / "media_factory_source.old"
    stale.write_bytes(b"stale")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x" * 2048)
    monkeypatch.setattr(shorts_factory, "DOWNLOAD_DIR", download_dir)

    destination = shorts_factory._persist_factory_source(source, "media")

    assert destination.read_bytes() == b"x" * 2048
    assert not stale.exists()
