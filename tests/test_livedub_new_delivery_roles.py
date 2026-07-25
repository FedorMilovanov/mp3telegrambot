from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from services import livedub_new_delivery_atomicity as atomicity


def _sender(monkeypatch):
    import services.livedub_audio_companion as companion

    async def legacy(*args, **kwargs):
        return True

    monkeypatch.setattr(companion, "_send_new_audio", legacy)
    atomicity._install_strict_new_audio()
    return companion, companion._send_new_audio


def test_derived_artifact_cannot_be_clean_ru_source(tmp_path: Path, monkeypatch):
    companion, sender = _sender(monkeypatch)
    video = tmp_path / "video.mp4"
    derived = tmp_path / "video.final-mix.mp3"
    video.write_bytes(b"video")
    derived.write_bytes(b"audio")

    monkeypatch.setattr(companion, "_dual_enabled", lambda: True)
    monkeypatch.setattr(companion, "_probe_audio", lambda _path: (True, 60))
    monkeypatch.setattr(companion, "_find_clean_ru_track", lambda _path: derived)
    monkeypatch.setattr(
        companion,
        "_extract_mix_mp3",
        lambda _path: pytest.fail("mix extraction must not start with an invalid clean role"),
    )

    class FakeBot:
        async def send_audio(self, **kwargs):
            pytest.fail("derived audio must not be sent as clean RU")

    with pytest.raises(RuntimeError, match="чистая русская дорожка не найдена"):
        asyncio.run(
            sender(
                FakeBot(),
                chat_id=1,
                video_path=video,
                caption="Название - Автор",
                reply_to=2,
                thumbnail=None,
                video_file_id="video-id",
            )
        )


def test_same_physical_file_cannot_fill_both_dual_roles(tmp_path: Path, monkeypatch):
    companion, sender = _sender(monkeypatch)
    video = tmp_path / "video.mp4"
    shared = tmp_path / "translation.live.mp3"
    video.write_bytes(b"video")
    shared.write_bytes(b"audio")

    monkeypatch.setattr(companion, "_dual_enabled", lambda: True)
    monkeypatch.setattr(companion, "_probe_audio", lambda _path: (True, 60))
    monkeypatch.setattr(companion, "_find_clean_ru_track", lambda _path: shared)
    monkeypatch.setattr(companion, "_extract_mix_mp3", lambda _path: shared)

    class FakeBot:
        async def send_audio(self, **kwargs):
            pytest.fail("duplicate physical source must be rejected before Telegram")

    with pytest.raises(RuntimeError, match="указывают на один файл"):
        asyncio.run(
            sender(
                FakeBot(),
                chat_id=1,
                video_path=video,
                caption="Название - Автор",
                reply_to=2,
                thumbnail=None,
                video_file_id="video-id",
            )
        )
