from pathlib import Path
from types import SimpleNamespace

import pytest

import pipelines.clips as clips
from services.media_delivery_probe import MediaProbe


def _candidate() -> dict:
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
    def __init__(self):
        self.videos: list[dict] = []
        self.texts: list[str] = []

    async def reply_video(self, **kwargs):
        self.videos.append(kwargs)
        return SimpleNamespace()

    async def reply_text(self, text, **kwargs):
        self.texts.append(str(text))
        return SimpleNamespace()


async def _configure_pipeline(monkeypatch, tmp_path: Path, *, probe):
    monkeypatch.setattr(clips, "DOWNLOAD_DIR", tmp_path)

    async def candidates(**kwargs):
        return [_candidate()]

    async def setting(key):
        assert key == "clips_snapshot"
        return False

    async def render(source, target, start, end):
        assert source.exists()
        target.write_bytes(b"rendered")
        return True

    async def probe_media(path):
        assert path.exists()
        return probe

    monkeypatch.setattr(clips, "create_clips_candidates", candidates)
    monkeypatch.setattr(clips, "asettings_get", setting)
    monkeypatch.setattr(clips, "render_clip", render)
    monkeypatch.setattr(clips, "probe_media_async", probe_media)
    monkeypatch.setattr(clips, "get_max_file_size_mb", lambda: 100.0)
    monkeypatch.setattr(clips, "settings_get", lambda key: False)
    monkeypatch.setattr(clips, "build_clip_caption", lambda **kwargs: "clip")


@pytest.mark.asyncio
async def test_clips_use_probed_duration_and_preserve_borrowed_livedub(
    monkeypatch, tmp_path
):
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
    assert livedub.exists(), "Clips must not unlink the borrowed LiveDub master"
    assert not (tmp_path / "abc_clip_1.mp4").exists()


@pytest.mark.asyncio
async def test_clips_reject_nonempty_render_without_delivery_probe(
    monkeypatch, tmp_path
):
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


def test_clips_delivery_boundary_uses_media_probe_and_borrowed_ownership():
    source = Path("pipelines/clips.py").read_text(encoding="utf-8")

    assert "media_probe_is_deliverable(clip_probe)" in source
    assert "delivery_duration = float(clip_probe.duration)" in source
    assert "duration=max(1, int(round(delivery_duration)))" in source
    assert "not _clips_keep and not borrowed_video" in source
