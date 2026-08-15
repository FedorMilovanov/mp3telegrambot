from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import services.livedub_audio_companion as companion
import services.livedub_audio_quality_guard as quality
import services.livedub_delivery_coordinator as coordinator


class FakeBot:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.sent_audio = []
        self.deleted = []

    async def send_audio(self, **kwargs):
        self.sent_audio.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(
            message_id=outcome[0],
            audio=SimpleNamespace(file_id=outcome[1]),
        )

    async def delete_message(self, *, chat_id, message_id):
        self.deleted.append((chat_id, message_id))
        return True


async def _fields(_card, variant):
    return {
        "title": "Title",
        "performer": "Author",
        "caption": f"caption:{variant}",
        "parse_mode": "HTML",
    }


def _reset_singleflight():
    coordinator._COMPANION_SENT.clear()
    coordinator._COMPANION_INFLIGHT.clear()


def _patch_media(monkeypatch, tmp_path: Path):
    video = tmp_path / "video.mp4"
    clean = tmp_path / "clean.mp3"
    mixed = tmp_path / "video.final-mix.mp3"
    for path in (video, clean, mixed):
        path.write_bytes(b"x" * 4096)
    monkeypatch.setattr(companion, "_probe_audio", lambda _path: (True, 60))
    monkeypatch.setattr(companion, "_extract_mix_mp3", lambda _path: mixed)
    monkeypatch.setattr(quality, "select_clean_translation_mp3", lambda _workdir: clean)
    monkeypatch.setattr(coordinator, "_publication_audio_fields", _fields)
    return video, clean, mixed


def test_new_companion_transaction_rolls_back_partial_send(monkeypatch, tmp_path):
    _reset_singleflight()
    video, _clean, _mixed = _patch_media(monkeypatch, tmp_path)
    bot = FakeBot([(101, "clean-id"), RuntimeError("mixed send failed")])
    puts = []
    drops = []
    monkeypatch.setattr(companion, "_cache_put_variant", lambda *a, **kw: puts.append((a, kw)))
    monkeypatch.setattr(companion, "_cache_drop", lambda key: drops.append(key))

    async def scenario():
        with pytest.raises(RuntimeError, match="mixed send failed"):
            await coordinator.deliver_new_companions(
                bot,
                chat_id=7,
                video_path=video,
                publication_card={},
                reply_to=9,
                thumbnail=None,
                video_file_id="video-id",
            )

    asyncio.run(scenario())
    assert puts == []
    assert drops == ["video-id"]
    assert bot.deleted == [(7, 101)]


def test_new_companion_transaction_commits_only_after_all_messages(monkeypatch, tmp_path):
    _reset_singleflight()
    video, _clean, _mixed = _patch_media(monkeypatch, tmp_path)
    bot = FakeBot([(101, "clean-id"), (102, "mixed-id")])
    state = {"variants": {}}

    def put(key, variant, audio_file_id, **meta):
        assert key == "video-id"
        state["variants"][variant] = {"audio_file_id": audio_file_id, **meta}

    monkeypatch.setattr(companion, "_cache_put_variant", put)
    monkeypatch.setattr(companion, "_cache_get", lambda key: state if key == "video-id" else None)
    monkeypatch.setattr(companion, "_cache_drop", lambda _key: (_ for _ in ()).throw(AssertionError("unexpected drop")))

    async def scenario():
        assert await coordinator.deliver_new_companions(
            bot,
            chat_id=7,
            video_path=video,
            publication_card={},
            reply_to=9,
            thumbnail=None,
            video_file_id="video-id",
        )

    asyncio.run(scenario())
    assert [item["audio_file_id"] for item in state["variants"].values()] == ["clean-id", "mixed-id"]
    assert bot.deleted == []


def test_cached_duplicate_role_ids_fail_before_send(monkeypatch):
    _reset_singleflight()
    bot = FakeBot([])
    dropped = []
    monkeypatch.setattr(
        companion,
        "_cache_get",
        lambda _key: {
            "variants": {
                "clean": {"audio_file_id": "same-id"},
                "mixed": {"audio_file_id": "same-id"},
            }
        },
    )
    monkeypatch.setattr(companion, "_cache_drop", lambda key: dropped.append(key))
    monkeypatch.setattr(coordinator, "_publication_audio_fields", _fields)

    async def scenario():
        with pytest.raises(RuntimeError, match="same audio_file_id"):
            await coordinator.deliver_cached_companions(
                bot,
                chat_id=7,
                video_file_id="video-id",
                publication_card={},
                reply_to=9,
            )

    asyncio.run(scenario())
    assert bot.sent_audio == []
    assert dropped == ["video-id"]


def test_cached_partial_send_is_rolled_back(monkeypatch):
    _reset_singleflight()
    bot = FakeBot([(201, "unused"), RuntimeError("stale mixed")])
    dropped = []
    monkeypatch.setattr(
        companion,
        "_cache_get",
        lambda _key: {
            "variants": {
                "clean": {"audio_file_id": "clean-id"},
                "mixed": {"audio_file_id": "mixed-id"},
            }
        },
    )
    monkeypatch.setattr(companion, "_cache_drop", lambda key: dropped.append(key))
    monkeypatch.setattr(coordinator, "_publication_audio_fields", _fields)

    async def scenario():
        with pytest.raises(RuntimeError, match="stale mixed"):
            await coordinator.deliver_cached_companions(
                bot,
                chat_id=7,
                video_file_id="video-id",
                publication_card={},
                reply_to=9,
            )

    asyncio.run(scenario())
    assert bot.deleted == [(7, 201)]
    assert dropped == ["video-id"]


def test_source_audio_deferral_is_request_scoped_and_discardable(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVEDUB_AUDIO_DEDUPE", "1")
    monkeypatch.setenv("LIVEDUB_SEND_AUDIO", "1")
    source = tmp_path / "source.mp3"
    source.write_bytes(b"source audio")

    class Message:
        def __init__(self):
            self.calls = 0

        async def reply_audio(self, **_kwargs):
            self.calls += 1
            raise AssertionError("source reply_audio must be deferred")

    class Bot:
        def __init__(self):
            self.calls = []

        async def send_audio(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(audio=SimpleNamespace(file_id="source-id"))

    message = Message()
    bot = Bot()

    async def scenario():
        delivery = coordinator.create_source_audio_deferral(
            bot=bot, chat_id=7, reply_to=9, enabled=True
        )
        placeholder = await delivery.send_or_defer(
            message,
            audio=source,
            fallback_path=source,
            title="Source",
        )
        assert placeholder.audio.file_id == ""
        assert delivery.has_pending
        delivery.discard("complete LiveDub delivered")
        await asyncio.sleep(0)
        assert not delivery.has_pending

    asyncio.run(scenario())
    assert message.calls == 0
    assert bot.calls == []


def test_source_audio_deferral_flushes_once(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVEDUB_AUDIO_DEDUPE", "1")
    monkeypatch.setenv("LIVEDUB_SEND_AUDIO", "1")
    source = tmp_path / "source.mp3"
    source.write_bytes(b"source audio")

    class Message:
        async def reply_audio(self, **_kwargs):
            raise AssertionError("source reply_audio must be deferred")

    class Bot:
        def __init__(self):
            self.calls = []

        async def send_audio(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(audio=SimpleNamespace(file_id="source-id"))

    bot = Bot()

    async def scenario():
        delivery = coordinator.create_source_audio_deferral(
            bot=bot, chat_id=7, reply_to=9, enabled=True
        )
        await delivery.send_or_defer(
            Message(),
            audio=source,
            fallback_path=source,
            title="Source",
        )
        assert await delivery.flush("LiveDub unavailable")
        assert not delivery.has_pending
        assert not await delivery.flush("second flush")

    asyncio.run(scenario())
    assert len(bot.calls) == 1
    assert bot.calls[0]["chat_id"] == 7
    assert bot.calls[0]["reply_to_message_id"] == 9
