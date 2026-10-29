import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from telegram.error import TimedOut

import pipelines.clips as clips
from services.media_delivery_probe import MediaProbe


def _candidate() -> dict[str, Any]:
    return {
        "start": "1:00",
        "end": "3:00",
        "start_seconds": 60,
        "end_seconds": 180,
        "duration_seconds": 120,
        "title": "Проверенный клип",
        "hashtags": [],
    }


def _deliverable_probe(duration: float) -> MediaProbe:
    return MediaProbe(
        duration=duration,
        width=1920,
        height=1080,
        audio_sample_rate=48000,
        audio_codec="aac",
        has_video=True,
        has_audio=True,
        size_mb=1.0,
    )


class _Message:
    def __init__(self) -> None:
        self.videos: list[dict[str, Any]] = []
        self.texts: list[str] = []

    async def reply_video(self, **kwargs: Any) -> SimpleNamespace:
        self.videos.append(kwargs)
        return SimpleNamespace()

    async def reply_text(self, text: object, **kwargs: Any) -> SimpleNamespace:
        self.texts.append(str(text))
        return SimpleNamespace()


class _TimeoutMessage(_Message):
    async def reply_video(self, **kwargs: Any) -> SimpleNamespace:
        self.videos.append(kwargs)
        raise TimedOut("Timed out")


async def _configure_pipeline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, probe: MediaProbe | None) -> None:
    monkeypatch.setattr(clips, "DOWNLOAD_DIR", tmp_path)

    async def candidates(**kwargs: Any) -> list[dict[str, Any]]:
        return [_candidate()]

    async def setting(key: str) -> bool:
        assert key == "clips_snapshot"
        return False

    async def render(source: Path, target: Path, start: float, end: float, **kwargs: Any) -> bool:
        assert await asyncio.to_thread(source.exists)
        await asyncio.to_thread(target.write_bytes, b"rendered")
        return True

    async def probe_media(path: Path) -> MediaProbe | None:
        assert await asyncio.to_thread(path.exists)
        return probe

    async def probe_livedub(source_path: Path, *, fallback_duration: float = 0.0) -> float:
        assert await asyncio.to_thread(source_path.exists)
        return 600.0

    monkeypatch.setattr(clips, "create_clips_candidates", candidates)
    monkeypatch.setattr(clips, "asettings_get", setting)
    monkeypatch.setattr(clips, "render_clip", render)
    monkeypatch.setattr(clips, "probe_media_async", probe_media)
    monkeypatch.setattr(clips, "probe_livedub_source_duration", probe_livedub)
    monkeypatch.setattr(clips, "get_max_file_size_mb", lambda: 100.0)
    monkeypatch.setattr(clips, "settings_get", lambda key: False)
    monkeypatch.setattr(clips, "build_clip_caption", lambda **kwargs: "clip")


@pytest.mark.asyncio
async def test_clips_use_probed_duration_and_preserve_borrowed_livedub(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    probe = _deliverable_probe(125.6)
    await _configure_pipeline(monkeypatch, tmp_path, probe=probe)

    livedub = tmp_path / "translated.mp4"
    livedub.write_bytes(b"owned-by-livedub")
    message = _Message()

    await clips.process_and_send_clips(
        url="https://example.invalid/video",
        media_id="abc",
        mp3_path=tmp_path / "audio.mp3",
        title="Title",
        performer="Author",
        duration=600,
        ai_data={},
        update=SimpleNamespace(message=message),
        livedub_video_path=livedub,
    )

    assert len(message.videos) == 1
    assert message.videos[0]["duration"] == 126
    assert message.videos[0]["write_timeout"] == 1800
    assert message.videos[0]["read_timeout"] == 1800
    assert message.videos[0]["connect_timeout"] == 120
    assert message.videos[0]["pool_timeout"] == 120
    assert livedub.exists(), "Clips must not unlink the borrowed LiveDub master"
    assert not (tmp_path / "abc_clip_1.mp4").exists()


@pytest.mark.asyncio
async def test_clips_timeout_is_unconfirmed_and_never_retried_blindly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _configure_pipeline(monkeypatch, tmp_path, probe=_deliverable_probe(120.0))

    livedub = tmp_path / "translated.mp4"
    livedub.write_bytes(b"owned-by-livedub")
    message = _TimeoutMessage()

    with caplog.at_level(logging.WARNING):
        result = await clips.process_and_send_clips(
            url="https://example.invalid/video",
            media_id="abc",
            mp3_path=tmp_path / "audio.mp3",
            title="Title",
            performer="Author",
            duration=600,
            ai_data={},
            update=SimpleNamespace(message=message),
            livedub_video_path=livedub,
        )

    assert result == 0
    assert len(message.videos) == 1, "A timed-out upload must not be duplicated automatically"
    assert "delivery unconfirmed 1/1" in caplog.text
    assert "no automatic retry to avoid duplicate delivery" in caplog.text
    assert "no clips confirmed delivered; unconfirmed uploads=1" in caplog.text


def test_factory_long_delivery_failure_does_not_claim_proven_non_delivery() -> None:
    source = Path("pipelines/shorts_factory.py").read_text(encoding="utf-8")

    assert "SHORTS FACTORY не подтвердил доставку ни одного длинного клипа" in source
    assert "SHORTS FACTORY не доставил ни одного длинного клипа" not in source


@pytest.mark.asyncio
async def test_clips_reject_nonempty_render_without_delivery_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await _configure_pipeline(monkeypatch, tmp_path, probe=None)

    livedub = tmp_path / "translated.mp4"
    livedub.write_bytes(b"owned-by-livedub")
    message = _Message()

    await clips.process_and_send_clips(
        url="https://example.invalid/video",
        media_id="abc",
        mp3_path=tmp_path / "audio.mp3",
        title="Title",
        performer="Author",
        duration=600,
        ai_data={},
        update=SimpleNamespace(message=message),
        livedub_video_path=livedub,
    )

    assert message.videos == []
    assert livedub.exists()


def test_clips_delivery_boundary_uses_media_probe_and_borrowed_ownership() -> None:
    source = Path("pipelines/clips.py").read_text(encoding="utf-8")

    assert "probe_livedub_source_duration(" in source
    assert "align_livedub_candidates(" in source
    assert "media_probe_is_deliverable(clip_probe)" in source
    assert "delivery_duration = float(clip_probe.duration)" in source
    assert "duration=max(1, int(round(delivery_duration)))" in source
    assert "if not keep and not borrowed_video" in source
