from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import services.cloud_media_fallback as fallback


class _CloudBot:
    base_url = "https://api.telegram.org/botTOKEN"

    async def send_video(self, chat_id, video, **kwargs):
        return {
            "chat_id": chat_id,
            "video": video,
            "caption": kwargs.get("caption"),
            "filename": kwargs.get("filename"),
            "width": kwargs.get("width"),
            "height": kwargs.get("height"),
            "duration": kwargs.get("duration"),
        }


class _LocalBot:
    base_url = "http://127.0.0.1:8081/botTOKEN"

    async def send_video(self, chat_id, video, **kwargs):
        return {
            "chat_id": chat_id,
            "video": video,
            "caption": kwargs.get("caption"),
            "filename": kwargs.get("filename"),
        }


def test_route_source_of_truth_is_actual_bot_base_url(monkeypatch):
    monkeypatch.setenv("MP3BOT_EFFECTIVE_BOT_API", "cloud")
    assert fallback._is_cloud_bot(_CloudBot()) is True
    assert fallback._is_cloud_bot(_LocalBot()) is False


def test_video_height_tracks_available_bitrate():
    assert fallback._video_height_for_bitrate(50) == 240
    assert fallback._video_height_for_bitrate(120) == 360
    assert fallback._video_height_for_bitrate(250) == 480
    assert fallback._video_height_for_bitrate(600) == 720
    assert fallback._video_height_for_bitrate(1200) == 1080


def test_long_video_bitrate_plan_stays_inside_calculated_budget():
    duration = 3 * 60 * 60
    max_mb = 47.0
    video_kbps, audio_kbps, _height = fallback._video_bitrate_plan(
        duration,
        max_mb,
    )
    estimated_bytes = (video_kbps + audio_kbps + 8) * 1000 / 8 * duration
    assert video_kbps >= 8
    assert audio_kbps >= 8
    assert estimated_bytes < max_mb * 1024 * 1024


def test_output_extensions_match_encoded_container(tmp_path):
    source_video = tmp_path / "lecture.mkv"
    source_audio = tmp_path / "lecture.wav"
    assert fallback._safe_output_path(
        source_video,
        "cloud47",
        extension=".mp4",
    ).suffix == ".mp4"
    assert fallback._safe_output_path(
        source_audio,
        "cloud47",
        extension=".mp3",
    ).suffix == ".mp3"


def test_cloud_send_replaces_path_preserves_name_and_refreshes_metadata(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "Красивое название.mp4"
    replacement = tmp_path / "Красивое название.cloud47.mp4"
    source.write_bytes(b"x" * 100)
    replacement.write_bytes(b"y" * 10)

    monkeypatch.setattr(fallback, "_enabled", lambda: True)
    monkeypatch.setattr(fallback, "_target_mb", lambda: 47.0)
    monkeypatch.setattr(
        fallback,
        "_under_limit",
        lambda path, _limit: path == replacement,
    )
    monkeypatch.setattr(fallback, "_transcode_video", lambda *_: replacement)
    monkeypatch.setattr(
        fallback,
        "_probe_media",
        lambda path: {
            "duration": 1818.2,
            "has_video": True,
            "has_audio": True,
            "width": 640,
            "height": 360,
        },
    )

    fallback._wrap_send_method(
        _CloudBot,
        "send_video",
        media_pos=1,
        kind="video",
    )
    result = asyncio.run(
        _CloudBot().send_video(
            123,
            source,
            caption="Готово",
            width=1920,
            height=1080,
            duration=1818,
        )
    )

    assert Path(result["video"]) == replacement
    assert result["filename"] == source.name
    assert result["width"] == 640
    assert result["height"] == 360
    assert result["duration"] == 1818
    assert "автоматически сжато" in result["caption"]


def test_local_send_keeps_original_file(tmp_path, monkeypatch):
    source = tmp_path / "large.mp4"
    source.write_bytes(b"x" * 100)
    calls = []

    monkeypatch.setattr(fallback, "_enabled", lambda: True)
    monkeypatch.setattr(fallback, "_target_mb", lambda: 47.0)
    monkeypatch.setattr(fallback, "_under_limit", lambda *_: False)
    monkeypatch.setattr(
        fallback,
        "_transcode_video",
        lambda *_: calls.append(True),
    )

    fallback._wrap_send_method(
        _LocalBot,
        "send_video",
        media_pos=1,
        kind="video",
    )
    result = asyncio.run(
        _LocalBot().send_video(123, source, caption="Готово")
    )

    assert Path(result["video"]) == source
    assert result["filename"] is None
    assert calls == []


def test_source_lock_registry_is_released(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x")
    key = str(source.resolve())
    with fallback._source_lock(source):
        assert key in fallback._TRANSCODE_LOCKS
    assert key not in fallback._TRANSCODE_LOCKS


def test_under_limit_rejects_empty_partial_file(tmp_path):
    empty = tmp_path / "broken.cloud47.mp4"
    empty.write_bytes(b"")
    assert fallback._under_limit(empty, 47.0) is False


def test_pipeline_adapter_bypasses_only_processing_guard(monkeypatch):
    import pipelines

    fake_pipeline = types.ModuleType("pipelines.main_pipeline")
    fake_pipeline.get_max_file_size_mb = lambda: 50
    monkeypatch.setitem(
        sys.modules,
        "pipelines.main_pipeline",
        fake_pipeline,
    )
    monkeypatch.setattr(
        pipelines,
        "main_pipeline",
        fake_pipeline,
        raising=False,
    )
    monkeypatch.setattr(
        fallback.shutil,
        "which",
        lambda name: name if name in {"ffmpeg", "ffprobe"} else None,
    )
    monkeypatch.setattr(fallback, "_enabled", lambda: True)
    monkeypatch.setenv("MP3BOT_EFFECTIVE_BOT_API", "cloud")

    fallback._install_pipeline_limit_adapter()

    assert fake_pipeline.get_max_file_size_mb() == 2000


def test_pipeline_adapter_does_not_override_real_local_limit(monkeypatch):
    import pipelines

    fake_pipeline = types.ModuleType("pipelines.main_pipeline")
    fake_pipeline.get_max_file_size_mb = lambda: 321
    monkeypatch.setitem(
        sys.modules,
        "pipelines.main_pipeline",
        fake_pipeline,
    )
    monkeypatch.setattr(
        pipelines,
        "main_pipeline",
        fake_pipeline,
        raising=False,
    )
    monkeypatch.setattr(
        fallback.shutil,
        "which",
        lambda name: name if name in {"ffmpeg", "ffprobe"} else None,
    )
    monkeypatch.setattr(fallback, "_enabled", lambda: True)
    monkeypatch.setenv("MP3BOT_EFFECTIVE_BOT_API", "local")

    fallback._install_pipeline_limit_adapter()

    assert fake_pipeline.get_max_file_size_mb() == 321
