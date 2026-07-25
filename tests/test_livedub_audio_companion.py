import asyncio
from pathlib import Path

import services.livedub_audio_companion as companion


class _Media:
    def __init__(self, file_id: str):
        self.file_id = file_id


class _Message:
    def __init__(self, *, video_id: str = "", audio_id: str = ""):
        self.video = _Media(video_id) if video_id else None
        self.audio = _Media(audio_id) if audio_id else None


def test_livedub_caption_detection_is_narrow():
    assert companion._is_livedub_caption("<b>Лекция</b>\n🎬 Живые голоса Яндекса")
    assert companion._is_livedub_caption("🎬 Перевод Яндекса (обычные голоса)")
    assert not companion._is_livedub_caption("Обычное видео с русскими субтитрами")
    assert not companion._is_livedub_caption("Живой перевод Яндекса недоступен")


def test_title_and_variant_filenames_are_human_readable(tmp_path: Path):
    title, performer = companion._title_parts(
        "<b>Как Мы Знаем То, Что Знаем? - Р. Ч. Спроул</b>\n🎬 Живые голоса Яндекса",
        "fallback",
    )
    assert title == "Как Мы Знаем То, Что Знаем?"
    assert performer == "Р. Ч. Спроул"
    clean_name = companion._safe_filename(tmp_path / "pro_dub.mp4", title, "clean")
    mixed_name = companion._safe_filename(tmp_path / "pro_dub.mp4", title, "mixed")
    assert clean_name.endswith("чистый RU.mp3")
    assert mixed_name.endswith("финальный микс.mp3")
    assert clean_name != mixed_name
    assert "?" not in clean_name
    assert "?" not in mixed_name


def test_file_id_cache_stores_two_independent_variants(tmp_path: Path, monkeypatch):
    cache_path = tmp_path / "audio-map.json"
    monkeypatch.setattr(companion, "_cache_path", lambda: cache_path)

    companion._cache_put_variant("video-1", "clean", "audio-clean", title="Title")
    companion._cache_put_variant("video-1", "mixed", "audio-mixed", title="Title")

    cached = companion._cache_get("video-1")
    assert cached["schema_version"] == 2
    assert cached["variants"]["clean"]["audio_file_id"] == "audio-clean"
    assert cached["variants"]["mixed"]["audio_file_id"] == "audio-mixed"

    companion._cache_drop_variant("video-1", "clean")
    cached = companion._cache_get("video-1")
    assert "clean" not in cached["variants"]
    assert cached["variants"]["mixed"]["audio_file_id"] == "audio-mixed"

    companion._cache_drop("video-1")
    assert companion._cache_get("video-1") is None


def test_successful_livedub_video_sends_clean_and_mixed_mp3(tmp_path: Path, monkeypatch):
    video = tmp_path / "Как Мы Знаем - Р. Ч. Спроул.mp4"
    video.write_bytes(b"video" * 1024)
    ru_audio = tmp_path / "translation.live.mp3"
    ru_audio.write_bytes(b"clean" * 1024)
    mixed_audio = tmp_path / "final.final-mix.mp3"
    mixed_audio.write_bytes(b"mixed" * 1024)
    cached = []

    monkeypatch.setattr(companion, "_dual_enabled", lambda: True)
    monkeypatch.setattr(companion, "_find_clean_ru_track", lambda _path: ru_audio)
    monkeypatch.setattr(companion, "_extract_mix_mp3", lambda _path: mixed_audio)
    monkeypatch.setattr(companion, "_probe_audio", lambda _path: (True, 1818))
    monkeypatch.setattr(
        companion,
        "_cache_put_variant",
        lambda video_id, variant, audio_id, **meta: cached.append(
            {"video": video_id, "variant": variant, "audio": audio_id, **meta}
        ),
    )

    class FakeBot:
        def __init__(self):
            self.audio_calls = []

        async def send_video(self, *args, **kwargs):
            return _Message(video_id="video-file-id")

        async def send_audio(self, *args, **kwargs):
            self.audio_calls.append(kwargs)
            return _Message(audio_id=f"audio-file-id-{len(self.audio_calls)}")

        async def send_message(self, *args, **kwargs):
            raise AssertionError("failure notice must not be sent")

    companion._wrap_send_video(FakeBot)
    bot = FakeBot()
    asyncio.run(
        bot.send_video(
            chat_id=10,
            video=video,
            caption="<b>Как Мы Знаем? - Р. Ч. Спроул</b>\n🎬 Живые голоса Яндекса",
            reply_to_message_id=20,
        )
    )

    assert len(bot.audio_calls) == 2
    clean_call, mixed_call = bot.audio_calls
    assert clean_call["audio"] == ru_audio
    assert mixed_call["audio"] == mixed_audio
    assert clean_call["duration"] == mixed_call["duration"] == 1818
    assert "Чистая аудиодорожка" in clean_call["caption"]
    assert "финального дубляжа" in mixed_call["caption"]
    assert clean_call["performer"] == mixed_call["performer"] == "Р. Ч. Спроул"
    assert [entry["variant"] for entry in cached] == ["clean", "mixed"]
    assert all(entry["video"] == "video-file-id" for entry in cached)


def test_cached_video_resends_both_paired_audio_file_ids(monkeypatch):
    monkeypatch.setattr(companion, "_dual_enabled", lambda: True)
    monkeypatch.setattr(
        companion,
        "_cache_get",
        lambda _video_id: {
            "schema_version": 2,
            "variants": {
                "clean": {
                    "audio_file_id": "cached-clean",
                    "title": "Название",
                    "performer": "Автор",
                },
                "mixed": {
                    "audio_file_id": "cached-mixed",
                    "title": "Название",
                    "performer": "Автор",
                },
            },
        },
    )

    class FakeBot:
        def __init__(self):
            self.audio_calls = []

        async def send_video(self, *args, **kwargs):
            return _Message(video_id="cached-video")

        async def send_audio(self, *args, **kwargs):
            self.audio_calls.append(kwargs)
            return _Message(audio_id=str(kwargs["audio"]))

        async def send_message(self, *args, **kwargs):
            return None

    companion._wrap_send_video(FakeBot)
    bot = FakeBot()
    asyncio.run(
        bot.send_video(
            chat_id=10,
            video="cached-video",
            caption="<b>Название - Автор</b>\n🎬 Живые голоса Яндекса",
        )
    )
    assert [call["audio"] for call in bot.audio_calls] == ["cached-clean", "cached-mixed"]
