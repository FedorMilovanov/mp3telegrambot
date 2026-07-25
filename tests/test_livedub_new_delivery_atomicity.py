from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import livedub_new_delivery_atomicity as atomicity


class _AudioMessage:
    def __init__(self, message_id: int, file_id: str) -> None:
        self.message_id = message_id
        self.audio = SimpleNamespace(file_id=file_id)


def _install(monkeypatch):
    import services.livedub_audio_companion as companion

    async def legacy(*args, **kwargs):
        return True

    monkeypatch.setattr(companion, "_send_new_audio", legacy)
    atomicity._install_strict_new_audio()
    return companion, companion._send_new_audio


def test_dual_mode_refuses_silent_single_mix_degradation(tmp_path: Path, monkeypatch):
    companion, sender = _install(monkeypatch)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")

    monkeypatch.setattr(companion, "_dual_enabled", lambda: True)
    monkeypatch.setattr(companion, "_probe_audio", lambda _path: (True, 120))
    monkeypatch.setattr(companion, "_find_clean_ru_track", lambda _path: None)
    monkeypatch.setattr(
        companion,
        "_extract_mix_mp3",
        lambda _path: pytest.fail("mixed extraction must not start without required clean RU"),
    )

    class FakeBot:
        async def send_audio(self, **kwargs):
            pytest.fail("no MP3 may be sent for an incomplete dual-source set")

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


def test_second_new_variant_failure_rolls_back_first_and_cache(tmp_path: Path, monkeypatch):
    companion, sender = _install(monkeypatch)
    video = tmp_path / "video.mp4"
    clean = tmp_path / "clean.mp3"
    mixed = tmp_path / "mixed.mp3"
    for path in (video, clean, mixed):
        path.write_bytes(b"media")

    monkeypatch.setattr(companion, "_dual_enabled", lambda: True)
    monkeypatch.setattr(companion, "_probe_audio", lambda _path: (True, 300))
    monkeypatch.setattr(companion, "_find_clean_ru_track", lambda _path: clean)
    monkeypatch.setattr(companion, "_extract_mix_mp3", lambda _path: mixed)
    dropped: list[str] = []
    committed: list[str] = []
    monkeypatch.setattr(companion, "_cache_drop", lambda video_id: dropped.append(video_id))
    monkeypatch.setattr(
        companion,
        "_cache_put_variant",
        lambda _video_id, variant, _audio_id, **_meta: committed.append(variant),
    )

    class FakeBot:
        def __init__(self) -> None:
            self.calls = 0
            self.deleted: list[tuple[int, int]] = []

        async def send_audio(self, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("Telegram upload failed")
            return _AudioMessage(101, "clean-file-id")

        async def delete_message(self, *, chat_id, message_id):
            self.deleted.append((chat_id, message_id))

    bot = FakeBot()
    with pytest.raises(RuntimeError, match="Telegram upload failed"):
        asyncio.run(
            sender(
                bot,
                chat_id=10,
                video_path=video,
                caption="Название - Автор",
                reply_to=20,
                thumbnail=None,
                video_file_id="video-id",
            )
        )

    assert bot.deleted == [(10, 101)]
    assert dropped == ["video-id"]
    assert committed == []


def test_new_dual_set_commits_only_after_both_messages_exist(tmp_path: Path, monkeypatch):
    companion, sender = _install(monkeypatch)
    video = tmp_path / "video.mp4"
    clean = tmp_path / "clean.mp3"
    mixed = tmp_path / "mixed.mp3"
    for path in (video, clean, mixed):
        path.write_bytes(b"media")

    monkeypatch.setattr(companion, "_dual_enabled", lambda: True)
    monkeypatch.setattr(companion, "_probe_audio", lambda _path: (True, 90))
    monkeypatch.setattr(companion, "_find_clean_ru_track", lambda _path: clean)
    monkeypatch.setattr(companion, "_extract_mix_mp3", lambda _path: mixed)
    events: list[str] = []
    monkeypatch.setattr(
        companion,
        "_cache_put_variant",
        lambda _video_id, variant, _audio_id, **_meta: events.append(f"cache:{variant}"),
    )

    class FakeBot:
        async def send_audio(self, **kwargs):
            variant = "clean" if kwargs["audio"] == clean else "mixed"
            events.append(f"send:{variant}")
            return _AudioMessage(len(events), f"{variant}-file-id")

    assert asyncio.run(
        sender(
            FakeBot(),
            chat_id=3,
            video_path=video,
            caption="Название - Автор",
            reply_to=4,
            thumbnail=None,
            video_file_id="video-id",
        )
    ) is True
    assert events == ["send:clean", "send:mixed", "cache:clean", "cache:mixed"]


def test_single_mode_can_fall_back_to_mixed_only(tmp_path: Path, monkeypatch):
    companion, sender = _install(monkeypatch)
    video = tmp_path / "video.mp4"
    mixed = tmp_path / "mixed.mp3"
    video.write_bytes(b"video")
    mixed.write_bytes(b"mixed")

    monkeypatch.setattr(companion, "_dual_enabled", lambda: False)
    monkeypatch.setattr(companion, "_probe_audio", lambda _path: (True, 45))
    monkeypatch.setattr(companion, "_find_clean_ru_track", lambda _path: None)
    monkeypatch.setattr(companion, "_extract_mix_mp3", lambda _path: mixed)
    sent: list[Path] = []
    monkeypatch.setattr(companion, "_cache_put_variant", lambda *args, **kwargs: None)

    class FakeBot:
        async def send_audio(self, **kwargs):
            sent.append(kwargs["audio"])
            return _AudioMessage(1, "mixed-id")

    assert asyncio.run(
        sender(
            FakeBot(),
            chat_id=1,
            video_path=video,
            caption="Название - Автор",
            reply_to=2,
            thumbnail=None,
            video_file_id="video-id",
        )
    ) is True
    assert sent == [mixed]
