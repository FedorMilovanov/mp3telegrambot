from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import services.cloud_media_fallback as fallback


class _CloudBot:
    base_url = "https://api.telegram.org/botTOKEN"

    async def send_video(self, chat_id, video, **kwargs):
        return {"chat_id": chat_id, "video": video, "caption": kwargs.get("caption")}


class _LocalBot:
    base_url = "http://127.0.0.1:8081/botTOKEN"

    async def send_video(self, chat_id, video, **kwargs):
        return {"chat_id": chat_id, "video": video, "caption": kwargs.get("caption")}


def test_route_source_of_truth_is_actual_bot_base_url(monkeypatch):
    monkeypatch.setenv("MP3BOT_EFFECTIVE_BOT_API", "cloud")
    assert fallback._is_cloud_bot(_CloudBot()) is True
    assert fallback._is_cloud_bot(_LocalBot()) is False


def test_video_height_tracks_available_bitrate():
    assert fallback._video_height_for_bitrate(120) == 360
    assert fallback._video_height_for_bitrate(250) == 480
    assert fallback._video_height_for_bitrate(600) == 720
    assert fallback._video_height_for_bitrate(1200) == 1080


def test_cloud_send_replaces_oversized_path_and_adds_note(tmp_path, monkeypatch):
    source = tmp_path / "large.mp4"
    replacement = tmp_path / "large.cloud48.mp4"
    source.write_bytes(b"x" * 100)
    replacement.write_bytes(b"y" * 10)

    monkeypatch.setattr(fallback, "_enabled", lambda: True)
    monkeypatch.setattr(fallback, "_target_mb", lambda: 48.0)
    monkeypatch.setattr(fallback, "_under_limit", lambda path, _limit: path == replacement)
    monkeypatch.setattr(fallback, "_transcode_video", lambda *_: replacement)

    fallback._wrap_send_method(_CloudBot, "send_video", media_pos=1, kind="video")
    result = asyncio.run(_CloudBot().send_video(123, source, caption="Готово"))

    assert Path(result["video"]) == replacement
    assert "автоматически сжато" in result["caption"]


def test_local_send_keeps_original_file(tmp_path, monkeypatch):
    source = tmp_path / "large.mp4"
    source.write_bytes(b"x" * 100)
    calls = []

    monkeypatch.setattr(fallback, "_enabled", lambda: True)
    monkeypatch.setattr(fallback, "_target_mb", lambda: 48.0)
    monkeypatch.setattr(fallback, "_under_limit", lambda *_: False)
    monkeypatch.setattr(fallback, "_transcode_video", lambda *_: calls.append(True))

    fallback._wrap_send_method(_LocalBot, "send_video", media_pos=1, kind="video")
    result = asyncio.run(_LocalBot().send_video(123, source, caption="Готово"))

    assert Path(result["video"]) == source
    assert calls == []


def test_pipeline_adapter_bypasses_only_processing_guard(monkeypatch):
    import pipelines

    fake_pipeline = types.ModuleType("pipelines.main_pipeline")
    fake_pipeline.get_max_file_size_mb = lambda: 50
    # `import pipelines.main_pipeline` can resolve through both sys.modules and
    # the cached attribute on the parent package. Replace both to emulate a
    # clean application import deterministically.
    monkeypatch.setitem(sys.modules, "pipelines.main_pipeline", fake_pipeline)
    monkeypatch.setattr(pipelines, "main_pipeline", fake_pipeline, raising=False)
    monkeypatch.setattr(fallback.shutil, "which", lambda name: "ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr(fallback, "_enabled", lambda: True)

    fallback._install_pipeline_limit_adapter()

    assert fake_pipeline.get_max_file_size_mb() == 2000
