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


def test_title_and_filename_are_human_readable(tmp_path: Path):
    title, performer = companion._title_parts(
        "<b>Как Мы Знаем То, Что Знаем? - Р. Ч. Спроул</b>\n🎬 Живые голоса Яндекса",
        "fallback",
    )
    assert title == "Как Мы Знаем То, Что Знаем?"
    assert performer == "Р. Ч. Спроул"
    assert companion._safe_filename(tmp_path / "pro_dub.mp4", title).endswith(".mp3")
    assert "?" not in companion._safe_filename(tmp_path / "pro_dub.mp4", title)


def test_file_id_cache_is_persistent_and_bounded(tmp_path: Path, monkeypatch):
    cache_path = tmp_path / "audio-map.json"
    monkeypatch.setattr(companion, "_cache_path", lambda: cache_path)
    companion._cache_put("video-1", "audio-1", title="Title")
    assert companion._cache_get("video-1")["audio_file_id"] == "audio-1"
    companion._cache_drop("video-1")
    assert companion._cache_get("video-1") is None


def test_successful_livedub_video_sends_clean_russian_mp3(tmp_path: Path, monkeypatch):
    video = tmp_path / "Как Мы Знаем - Р. Ч. Спроул.mp4"
    video.write_bytes(b"video")
    ru_audio = tmp_path / "translation.live.mp3"
    ru_audio.write_bytes(b"audio")
    cached = {}

    monkeypatch.setattr(companion, "_find_clean_ru_track", lambda _path: ru_audio)
    monkeypatch.setattr(companion, "_probe_audio", lambda _path: (True, 1818))
    monkeypatch.setattr(companion, "_cache_put", lambda video_id, audio_id, **meta: cached.update({"v": video_id, "a": audio_id, **meta}))

    class FakeBot:
        def __init__(self):
            self.audio_calls = []

        async def send_video(self, *args, **kwargs):
            return _Message(video_id="video-file-id")

        async def send_audio(self, *args, **kwargs):
            self.audio_calls.append(kwargs)
            return _Message(audio_id="audio-file-id")

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

    assert len(bot.audio_calls) == 1
    call = bot.audio_calls[0]
    assert call["audio"] == ru_audio
    assert call["duration"] == 1818
    assert "Чистая аудиодорожка" in call["caption"]
    assert call["performer"] == "Р. Ч. Спроул"
    assert cached["v"] == "video-file-id"
    assert cached["a"] == "audio-file-id"


def test_cached_video_resends_paired_audio_file_id(monkeypatch):
    monkeypatch.setattr(
        companion,
        "_cache_get",
        lambda _video_id: {
            "audio_file_id": "cached-audio",
            "title": "Название",
            "performer": "Автор",
        },
    )

    class FakeBot:
        def __init__(self):
            self.audio_calls = []

        async def send_video(self, *args, **kwargs):
            return _Message(video_id="cached-video")

        async def send_audio(self, *args, **kwargs):
            self.audio_calls.append(kwargs)
            return _Message(audio_id="cached-audio")

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
    assert bot.audio_calls[0]["audio"] == "cached-audio"
