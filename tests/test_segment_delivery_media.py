from pathlib import Path
from types import SimpleNamespace

import pytest

from core.segment_planner import PlannedSegment
from services import segment_render


class _ReplyTarget:
    def __init__(self):
        self.videos = []
        self.texts = []

    async def reply_video(self, **kwargs):
        self.videos.append(kwargs)
        return kwargs

    async def reply_text(self, text, **kwargs):
        self.texts.append(text)
        return text


class _Status:
    async def edit_text(self, text, **kwargs):
        return text


@pytest.mark.asyncio
async def test_segment_delivery_uses_actual_probed_duration(monkeypatch, tmp_path):
    monkeypatch.setattr(segment_render, "DOWNLOAD_DIR", tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    async def fake_download(_url, _video_id):
        return source

    async def fake_render(_source, output, _start, _end):
        Path(output).write_bytes(b"rendered" * 300)
        return True

    probe = SimpleNamespace(
        duration=123.4,
        has_video=True,
        has_audio=True,
        width=1280,
        height=720,
        audio_sample_rate=48000,
        audio_codec="aac",
    )

    async def fake_probe(_path):
        return probe

    async def fake_setting(_key):
        return False

    monkeypatch.setattr(segment_render, "download_video_for_shorts", fake_download)
    monkeypatch.setattr(segment_render, "render_clip", fake_render)
    monkeypatch.setattr(segment_render, "probe_media_async", fake_probe)
    monkeypatch.setattr(segment_render, "media_probe_is_deliverable", lambda value: value is probe)
    monkeypatch.setattr(segment_render, "asettings_get", fake_setting)
    monkeypatch.setattr(segment_render, "get_max_file_size_mb", lambda: 100)

    reply = _ReplyTarget()
    result = await segment_render.render_and_send_segment(
        reply_target=reply,
        status_msg=_Status(),
        video_id="video",
        source_url="https://example.test/video",
        segment=PlannedSegment(index=1, start=10, end=130, title="Тема"),
        title="Материал",
        total_segments=1,
    )

    assert result is True
    assert len(reply.videos) == 1
    assert reply.videos[0]["duration"] == 123


@pytest.mark.asyncio
async def test_segment_rejects_base_render_without_video_and_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(segment_render, "DOWNLOAD_DIR", tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    async def fake_download(_url, _video_id):
        return source

    async def fake_render(_source, output, _start, _end):
        Path(output).write_bytes(b"rendered" * 300)
        return True

    async def fake_probe(_path):
        return None

    monkeypatch.setattr(segment_render, "download_video_for_shorts", fake_download)
    monkeypatch.setattr(segment_render, "render_clip", fake_render)
    monkeypatch.setattr(segment_render, "probe_media_async", fake_probe)
    monkeypatch.setattr(segment_render, "media_probe_is_deliverable", lambda _value: False)

    reply = _ReplyTarget()
    result = await segment_render.render_and_send_segment(
        reply_target=reply,
        status_msg=_Status(),
        video_id="video",
        source_url="https://example.test/video",
        segment=PlannedSegment(index=1, start=10, end=130, title="Тема"),
        title="Материал",
        total_segments=1,
    )

    assert result is False
    assert reply.videos == []
    assert any("видео и звука" in text for text in reply.texts)


def test_segment_pipeline_contains_final_delivery_fallback():
    source = Path("services/segment_render.py").read_text(encoding="utf-8")

    assert "select_delivery_file(" in source
    assert "media_probe_is_deliverable(raw_probe)" in source
    assert "media_probe_is_deliverable(final_probe)" in source
    assert "delivery_duration = float(final_probe.duration)" in source
