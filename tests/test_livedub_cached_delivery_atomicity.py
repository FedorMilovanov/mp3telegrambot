from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import livedub_cached_delivery_atomicity as atomicity


class _Message:
    def __init__(self, message_id: int, *, video_file_id: str = "") -> None:
        self.message_id = message_id
        self.video = SimpleNamespace(file_id=video_file_id) if video_file_id else None


def test_partial_cached_mp3_send_rolls_back_prior_message(monkeypatch):
    import services.livedub_audio_companion as companion

    monkeypatch.setattr(
        companion,
        "_cache_get",
        lambda _video_id: {
            "variants": {
                "clean": {
                    "audio_file_id": "clean-id",
                    "title": "Название",
                    "performer": "Автор",
                },
                "mixed": {
                    "audio_file_id": "mixed-expired",
                    "title": "Название",
                    "performer": "Автор",
                },
            }
        },
    )
    monkeypatch.setattr(companion, "_dual_enabled", lambda: True)
    dropped: list[tuple[str, str]] = []
    monkeypatch.setattr(
        companion,
        "_cache_drop_variant",
        lambda video_id, variant: dropped.append((video_id, variant)),
    )

    class FakeBot:
        def __init__(self) -> None:
            self.deleted: list[tuple[int, int]] = []

        async def send_audio(self, **kwargs):
            if kwargs["audio"] == "mixed-expired":
                raise RuntimeError("wrong file identifier")
            return _Message(101)

        async def delete_message(self, *, chat_id, message_id):
            self.deleted.append((chat_id, message_id))

    monkeypatch.setattr(companion, "_send_cached_audio", lambda *args, **kwargs: None)
    atomicity._install_strict_cached_audio()
    bot = FakeBot()

    with pytest.raises(RuntimeError, match="кэшированный комплект MP3 неполон"):
        asyncio.run(
            companion._send_cached_audio(
                bot,
                chat_id=10,
                video_file_id="video-id",
                reply_to=20,
            )
        )

    assert bot.deleted == [(10, 101)]
    assert dropped == [("video-id", "mixed")]


def test_cached_video_is_deleted_before_rebuild_signal():
    import services.livedub_deep_audit as deep

    deleted: list[tuple[int, int]] = []

    class FakeBot:
        async def send_video(self, *args, **kwargs):
            deep._MP3_COMPANION_FAILED.set(True)
            return _Message(501, video_file_id="cached-video")

        async def delete_message(self, *, chat_id, message_id):
            deleted.append((chat_id, message_id))

    atomicity._atomic_video_guard(FakeBot)
    bot = FakeBot()

    with pytest.raises(RuntimeError, match="cached messages rolled back"):
        asyncio.run(
            bot.send_video(
                chat_id=77,
                video="cached-video-file-id",
                caption="LiveDub",
            )
        )

    assert deleted == [(77, 501)]


def test_new_local_video_is_not_deleted_when_mp3_companion_fails():
    import services.livedub_deep_audit as deep

    class FakePath:
        def read(self):
            return b"video"

    class FakeBot:
        def __init__(self) -> None:
            self.deleted = False

        async def send_video(self, *args, **kwargs):
            deep._MP3_COMPANION_FAILED.set(True)
            return _Message(601, video_file_id="new-video")

        async def delete_message(self, **kwargs):
            self.deleted = True

    atomicity._atomic_video_guard(FakeBot)
    bot = FakeBot()
    result = asyncio.run(bot.send_video(chat_id=1, video=FakePath()))

    assert result.video.file_id == ""
    assert bot.deleted is False


def test_entrypoint_installs_atomicity_before_wrappers_capture_cached_send():
    source = Path("bot_new.py").read_text(encoding="utf-8")
    companion = source.index("install_livedub_audio_companion()")
    atomic = source.index("install_livedub_cached_delivery_atomicity()")
    quality = source.index("install_livedub_audio_quality_guard()")
    dedupe = source.index("install_livedub_audio_dedupe()")
    deep = source.index("install_livedub_deep_audit()")
    assert companion < atomic < quality < dedupe < deep
