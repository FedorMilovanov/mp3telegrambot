from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core import utils as core_utils
from handlers import callbacks


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
    assert not list(tmp_path.glob("*_trim.mp4"))


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
