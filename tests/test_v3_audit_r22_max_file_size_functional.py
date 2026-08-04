#!/usr/bin/env python3
"""AUDIT R22 (regression audit): the R21 fix's whole premise was that
MAX_FILE_SIZE_MB must be read dynamically at send-time rather than frozen
at import. tests/test_v3_audit_r21_max_file_size_dynamic.py only checks
this by grepping source text for the absence of a bare `MAX_FILE_SIZE_MB`
identifier — it never actually drives a real consumer's accept/reject
decision. This test exercises services/segment_render.py::
render_and_send_segment end-to-end with a controlled-size fake clip and
proves the runtime-mutable limit is actually honored, not just imported
correctly.
"""
import core.database as db
import pytest
from core.segment_planner import PlannedSegment
from services.media_delivery_probe import MediaProbe
from services.segment_render import render_and_send_segment


class _FakeMsg:
    def __init__(self):
        self.texts = []
        self.videos = []

    async def edit_text(self, text, **kw):
        pass

    async def reply_text(self, text, **kw):
        self.texts.append(text)

    async def reply_video(self, **kw):
        self.videos.append(kw)


async def _fake_asettings_get(key):
    return False  # no subtitles — keep the test focused on the size gate


def _make_fake_render(size_mb: int):
    async def _fake_render(video_path, clip_path, start, end):
        clip_path.write_bytes(b"0" * (size_mb * 1024 * 1024))
        return True
    return _fake_render


@pytest.mark.asyncio
async def test_oversized_segment_rejected_under_cloud_limit_but_accepted_under_local(
    tmp_path, monkeypatch
):
    """A 60MB rendered clip must be rejected when the bot is effectively on
    the 50MB cloud Bot API, and accepted once the SAME code detects it's
    effectively on the 2000MB local Bot API — without restarting the
    process or re-importing anything, exactly per the R21 fix's contract."""
    segment = PlannedSegment(index=1, start=0, end=10, title="Тестовый сегмент")

    async def fake_download(url, vid):
        return tmp_path / "src.mp4"

    monkeypatch.setattr("services.segment_render.download_video_for_shorts", fake_download)
    monkeypatch.setattr("services.segment_render.render_clip", _make_fake_render(60))
    monkeypatch.setattr("services.segment_render.asettings_get", _fake_asettings_get)
    monkeypatch.setattr("services.segment_render.DOWNLOAD_DIR", tmp_path)

    async def fake_probe(path):
        return MediaProbe(
            duration=10.0,
            width=1920,
            height=1080,
            audio_sample_rate=48000,
            audio_codec="aac",
            has_video=True,
            has_audio=True,
            size_mb=path.stat().st_size / (1024 * 1024),
        )

    monkeypatch.setattr("services.segment_render.probe_media_async", fake_probe)

    was_explicit = db._MAX_FILE_SIZE_MB_EXPLICIT
    was_value = db.MAX_FILE_SIZE_MB
    db._MAX_FILE_SIZE_MB_EXPLICIT = False
    try:
        db.set_effective_max_file_size_mb(False)  # cloud fallback: 50MB
        cloud_reply = _FakeMsg()
        ok_cloud = await render_and_send_segment(
            reply_target=cloud_reply, status_msg=_FakeMsg(),
            video_id="vid_cloud", source_url="http://example.invalid/v",
            segment=segment, title="Заголовок", total_segments=1,
        )
        assert ok_cloud is False
        assert not cloud_reply.videos
        assert any("слишком большой" in t for t in cloud_reply.texts)

        db.set_effective_max_file_size_mb(True)  # local Bot API confirmed live: 2000MB
        local_reply = _FakeMsg()
        ok_local = await render_and_send_segment(
            reply_target=local_reply, status_msg=_FakeMsg(),
            video_id="vid_local", source_url="http://example.invalid/v",
            segment=segment, title="Заголовок", total_segments=1,
        )
        assert ok_local is True
        assert local_reply.videos
        assert not any("слишком большой" in t for t in local_reply.texts)
    finally:
        db._MAX_FILE_SIZE_MB_EXPLICIT = was_explicit
        db.MAX_FILE_SIZE_MB = was_value
