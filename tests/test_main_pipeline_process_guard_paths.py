#!/usr/bin/env python3
"""Точки отказа `process_single_video`: что именно видит пользователь.

Эти ветки — самые дешёвые и самые частые: live-трансляция, слишком длинное
видео, мало места, битые метаданные. Проверяются здесь без сети, без yt-dlp и
без Telegram: подменяется только получение метаданных, остальное — настоящий код
пайплайна. Именно поэтому тест ловит разрыв между гвардом и ответом в чат.
"""
from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace

import pytest

# Прямой импорт, а не importorskip: если тяжёлый модуль перестанет
# импортироваться, тесты должны упасть, а не тихо пропуститься.
from pipelines import main_pipeline  # noqa: E402

VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


class _FakeStatus:
    """Минимальный «статус-месседж»: пайплайн только редактирует его текст."""


class _FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text, **_kwargs):
        self.replies.append(text)
        return _FakeStatus()


class _FakeUser:
    id = 4242


class _FakeUpdate:
    def __init__(self) -> None:
        self.message = _FakeMessage()
        self.effective_user = _FakeUser()


@pytest.fixture(autouse=True)
def _no_progress(monkeypatch: pytest.MonkeyPatch):
    """`set_progress` трогает Telegram — в этих тестах он не нужен."""

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main_pipeline, "set_progress", noop)


def _run(
    update: _FakeUpdate, *, silent_errors: bool = False, context=None
) -> bool:
    return asyncio.run(
        main_pipeline.process_single_video(
            VIDEO_URL,
            update,
            status_msg=_FakeStatus(),
            context=context,
            silent_errors=silent_errors,
        )
    )


def test_live_stream_is_refused_with_explanation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def live_info(_url, _timeout):
        return {"id": "x", "title": "Прямой эфир", "is_live": True}

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", live_info)
    update = _FakeUpdate()

    assert _run(update) is False
    assert len(update.message.replies) == 1
    assert "Live-трансляции не поддерживаются" in update.message.replies[0]


def test_silent_mode_does_not_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тихий режим нужен плейлистам: сообщение на каждое видео — спам."""

    async def live_info(_url, _timeout):
        return {"id": "x", "is_live": True}

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", live_info)
    update = _FakeUpdate()

    assert _run(update, silent_errors=True) is False
    assert update.message.replies == []


def test_too_long_video_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def long_info(_url, _timeout):
        return {"id": "x", "title": "Длинная проповедь", "duration": 7200}

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", long_info)
    monkeypatch.setenv("MAX_VIDEO_DURATION_SEC", "3600")
    update = _FakeUpdate()

    assert _run(update) is False
    assert "превышает лимит 60 мин" in update.message.replies[0]


def test_video_within_limit_is_not_refused_by_duration(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """Граница: ровно лимит — ещё можно."""

    async def info(_url, _timeout):
        return {"id": "x", "title": "Ровно час", "duration": 3600}

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", info)
    monkeypatch.setenv("MAX_VIDEO_DURATION_SEC", "3600")

    update = _FakeUpdate()
    # Дальше нужен реальный Telegram и БД, поэтому пайплайн упадёт — важно, что
    # отказ пришёл НЕ по длительности.
    assert _run(update) is False
    assert all("превышает лимит" not in reply for reply in update.message.replies)


def test_low_disk_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def info(_url, _timeout):
        return {"id": "x", "title": "Видео", "duration": 600}

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", info)
    monkeypatch.setattr(main_pipeline, "lowest_free_gb", lambda *_paths: 0.4)
    update = _FakeUpdate()

    assert _run(update) is False
    assert "Мало места на диске (0.4 ГБ свободно)" in update.message.replies[0]


def test_temp_dir_is_checked_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """ENG-режимы пишут видео в %TEMP%: свободное место там тоже важно.

    Проверка идёт по худшему из двух дисков, а не только по `DOWNLOAD_DIR`.
    """
    import shutil
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")

    async def info(_url, _timeout):
        return {"id": "x", "title": "Видео", "duration": 600}

    def fake_disk_usage(path):
        # В каталоге загрузок места много, во временном — почти нет.
        free = 50 * 1024**3 if path == main_pipeline.DOWNLOAD_DIR else 0.2 * 1024**3
        return usage(total=free, used=0, free=free)

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", info)
    monkeypatch.setattr(shutil, "disk_usage", fake_disk_usage)
    update = _FakeUpdate()

    assert _run(update) is False
    assert "Мало места на диске (0.2 ГБ свободно)" in update.message.replies[0]


def test_unknown_disk_space_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    async def info(_url, _timeout):
        return {"id": "x", "title": "Видео", "duration": 600}

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", info)
    monkeypatch.setattr(main_pipeline, "lowest_free_gb", lambda *_paths: None)

    update = _FakeUpdate()
    assert _run(update) is False
    assert all("Мало места" not in reply for reply in update.message.replies)


def test_missing_metadata_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_info(_url, _timeout):
        return None

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    async def fake_process(*_args, **_kwargs):
        return _Proc()

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", no_info)
    monkeypatch.setattr(main_pipeline, "run_cancellable_process", fake_process)

    update = _FakeUpdate()
    # Контракт `process_single_video`: не бросает наружу, а сообщает и отдаёт False.
    assert _run(update) is False
    assert "Не удалось получить метаданные видео" in update.message.replies[0]


def test_subprocess_error_shows_stderr_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пользователь/лог должны увидеть причину от yt-dlp, а не «info error»."""

    async def no_info(_url, _timeout):
        return None

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "ERROR: Video unavailable"

    async def fake_process(*_args, **_kwargs):
        return _Proc()

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", no_info)
    monkeypatch.setattr(main_pipeline, "run_cancellable_process", fake_process)

    update = _FakeUpdate()
    assert _run(update) is False
    assert "Video unavailable" in update.message.replies[0]


def test_metadata_timeout_hints_at_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Таймаут метаданных почти всегда про proxy — подсказка должна быть в тексте."""

    async def no_info(_url, _timeout):
        return None

    async def slow_process(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="yt-dlp", timeout=180)

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", no_info)
    monkeypatch.setattr(main_pipeline, "run_cancellable_process", slow_process)
    monkeypatch.setenv("YTDLP_INFO_TIMEOUT", "180")

    update = _FakeUpdate()
    assert _run(update) is False
    message = update.message.replies[0]
    assert "180" in message
    assert "YTDLP_PROXY_URL" in message


def test_error_reply_masks_gemini_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ключ из текста ошибки Google не должен уйти пользователю в чат."""
    secret = "AIza" + "B" * 35

    async def broken_info(_url, _timeout):
        raise RuntimeError(f"403 API key not valid: {secret}")

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", broken_info)
    update = _FakeUpdate()

    assert _run(update) is False
    reply = update.message.replies[0]
    assert secret not in reply
    assert "***APIKEY***" in reply

# ── ENG-режимы: отказ до скачивания ────────────────────────────────────────


class _FakeRow(tuple):
    """Строка sqlite: `row[0]` — значение настройки."""


class _FakeConn:
    def __init__(self, mode: str) -> None:
        self._mode = mode

    def execute(self, _sql, params):
        assert params == (f"user_mode_{_FakeUser.id}",)
        return self

    def fetchone(self):
        return _FakeRow((self._mode,))

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _eng_environment(monkeypatch: pytest.MonkeyPatch, mode: str) -> list[str]:
    """Подготовить ENG-режим без сети: БД отдаёт режим, VOT-клиента нет."""
    cleaned: list[str] = []

    async def info(_url, _timeout):
        return {"id": "media1", "title": "Sermon", "duration": 600}

    async def no_cache(_media_id):
        return None

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", info)
    monkeypatch.setattr(main_pipeline, "_db_conn", lambda: _FakeConn(mode))
    monkeypatch.setattr(main_pipeline, "adb_get", no_cache)
    monkeypatch.setattr(main_pipeline, "current_livedub_file_id_cache_version", lambda: "v1")
    monkeypatch.setattr(main_pipeline, "cleanup_files", cleaned.append)
    monkeypatch.setattr(main_pipeline, "lowest_free_gb", lambda *_paths: 50.0)

    import services.yandex_live_dub as live_dub

    monkeypatch.setattr(live_dub, "_ensure_vot_helper", lambda: False)

    def no_client():
        raise RuntimeError("нет vot-cli")

    monkeypatch.setattr(live_dub, "_check_vot_cli", no_client)
    return cleaned


@pytest.mark.parametrize("mode", ["eng_fast", "eng_fast_qa"])
def test_eng_quick_without_vot_client_is_refused_before_download(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Quick-режимы без клиента VOT падают сразу, а не после загрузки сотен МБ."""
    cleaned = _eng_environment(monkeypatch, mode)
    update = _FakeUpdate()

    assert _run(update) is False
    assert "нет клиента для Яндекс LiveDub" in update.message.replies[0]
    assert cleaned == ["media1"]


def test_eng_quick_refusal_is_silent_in_playlist_mode(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    _eng_environment(monkeypatch, "eng_fast")
    update = _FakeUpdate()

    assert _run(update, silent_errors=True) is False
    assert update.message.replies == []


def test_eng_quick_cached_path_executes_extracted_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cached file_id reaches the new stage without VOT, network, or Telegram."""
    cleaned = _eng_environment(monkeypatch, "eng_fast")

    async def cached_livedub(_media_id):
        return {"livedub_file_id_version": "v1", "livedub_file_id": "cached-video"}

    monkeypatch.setattr(main_pipeline, "adb_get", cached_livedub)
    update = _FakeUpdate()

    # context=None makes the real nested sender report False. The extracted
    # stage must explain that failure, run the caller's cleanup callback, and
    # return the same False through process_single_video.
    assert _run(update) is False
    assert cleaned == ["media1"]
    assert len(update.message.replies) == 1
    assert "Перевод «Живые голоса» не получился" in update.message.replies[0]


def test_eng_quick_cached_file_id_delivers_publication_and_companions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cached video executes publication preparation and the real sender."""
    cleaned = _eng_environment(monkeypatch, "eng_fast")

    import services.livedub_delivery_coordinator as coordinator
    import services.livedub_publication as publication

    card = {"title": "Translated <Title>", "author": "Author"}
    card_calls = []
    caption_calls = []
    companion_calls = []

    class Status:
        def __init__(self):
            self.edits = []
            self.deleted = False

        async def edit_text(self, text):
            self.edits.append(text)

        async def delete(self):
            self.deleted = True

    class Bot:
        def __init__(self):
            self.videos = []

        async def send_video(self, **kwargs):
            self.videos.append(kwargs)
            return SimpleNamespace(message_id=999)

    async def cached_livedub(_media_id):
        return {"livedub_file_id_version": "v1", "livedub_file_id": "cached-video"}

    async def build_card(source_line, source_url):
        card_calls.append((source_line, source_url))
        return card

    def format_caption(got_card, internal_caption):
        caption_calls.append((got_card, internal_caption))
        return "formatted caption"

    async def companions(bot, **kwargs):
        companion_calls.append((bot, kwargs))
        return True

    monkeypatch.setattr(main_pipeline, "adb_get", cached_livedub)
    monkeypatch.setattr(publication, "build_publication_card", build_card)
    monkeypatch.setattr(publication, "format_video_caption", format_caption)
    monkeypatch.setattr(coordinator, "deliver_cached_companions", companions)
    update = _FakeUpdate()
    update.effective_chat = SimpleNamespace(id=31337)
    update.message.message_id = 777
    status = Status()
    bot = Bot()

    result = asyncio.run(main_pipeline.process_single_video(
        VIDEO_URL,
        update,
        status_msg=status,
        context=SimpleNamespace(bot=bot),
    ))

    assert result is True
    assert cleaned == ["media1"]
    assert update.message.replies == []
    assert len(status.edits) == 1
    assert "ENG Quick" in status.edits[0]
    assert status.deleted is True
    assert card_calls == [("Sermon", VIDEO_URL)]
    assert len(caption_calls) == 1
    assert caption_calls[0][0] is card
    assert caption_calls[0][1] == (
        "<b>Translated &lt;Title&gt; - Author</b>\n🎬 Живые голоса Яндекса"
    )
    assert bot.videos == [{
        "chat_id": 31337,
        "video": "cached-video",
        "caption": "formatted caption",
        "parse_mode": "HTML",
        "reply_to_message_id": 777,
        "supports_streaming": True,
    }]
    assert companion_calls == [(
        bot,
        {
            "chat_id": 31337,
            "video_file_id": "cached-video",
            "publication_card": card,
            "reply_to": 777,
        },
    )]


def test_cached_file_id_pair_failure_invalidates_and_notifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected cached pair is deleted, invalidated, flushed, and explained."""
    cleaned = _eng_environment(monkeypatch, "eng_fast")

    import core.database as database
    import services.livedub_delivery_coordinator as coordinator
    import services.livedub_publication as publication

    card = {"title": "Cached title", "author": "Author"}
    video_message = SimpleNamespace(message_id=999)
    delete_calls = []
    invalidation_calls = []
    flush_calls = []
    discard_calls = []

    class Bot:
        def __init__(self):
            self.videos = []
            self.messages = []

        async def send_video(self, **kwargs):
            self.videos.append(kwargs)
            return video_message

        async def send_message(self, **kwargs):
            self.messages.append(kwargs)

    class Deferral:
        has_pending = False

        async def flush(self, reason):
            flush_calls.append(reason)

        def discard(self, reason):
            discard_calls.append(reason)

    async def cached_livedub(_media_id):
        return {"livedub_file_id_version": "v1", "livedub_file_id": "cached-video"}

    async def prepared(**_kwargs):
        return card, "Cached title - Author", "Cached title - Author"

    async def companions(*_args, **_kwargs):
        return False

    async def delete_message(*args):
        delete_calls.append(args)

    async def invalidate(*args):
        invalidation_calls.append(args)

    async def quick(**kwargs):
        delivered = await kwargs["send_result"]()
        kwargs["cleanup"](kwargs["media_id"])
        return delivered

    bot = Bot()
    deferral = Deferral()
    monkeypatch.setattr(main_pipeline, "adb_get", cached_livedub)
    monkeypatch.setattr(main_pipeline, "prepare_livedub_publication", prepared)
    monkeypatch.setattr(main_pipeline, "finish_livedub_quick", quick)
    monkeypatch.setattr(publication, "format_video_caption", lambda *_args: "caption")
    monkeypatch.setattr(coordinator, "deliver_cached_companions", companions)
    monkeypatch.setattr(coordinator, "delete_message_best_effort", delete_message)
    monkeypatch.setattr(coordinator, "create_source_audio_deferral", lambda **_kwargs: deferral)
    monkeypatch.setattr(database, "adb_set_livedub_file_id", invalidate)
    update = _FakeUpdate()
    update.effective_chat = SimpleNamespace(id=31337)
    update.message.message_id = 777

    result = asyncio.run(main_pipeline.process_single_video(
        VIDEO_URL,
        update,
        status_msg=_FakeStatus(),
        context=SimpleNamespace(bot=bot),
    ))

    assert result is True
    assert cleaned == ["media1"]
    assert len(bot.videos) == 1
    assert delete_calls == [(bot, 31337, video_message)]
    assert invalidation_calls == [("media1", "")]
    assert flush_calls == ["cached LiveDub pair unavailable"]
    assert discard_calls == []
    assert len(bot.messages) == 1
    assert bot.messages[0]["chat_id"] == 31337
    assert bot.messages[0]["reply_to_message_id"] == 777
    assert "MP3-комплект устарел" in bot.messages[0]["text"]
    assert update.message.replies == []


def _stop_at_delivery_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.livedub_delivery_coordinator as coordinator

    def stop(**_kwargs):
        raise RuntimeError("stop at delivery boundary")

    monkeypatch.setattr(coordinator, "create_source_audio_deferral", stop)


def test_missing_status_message_is_created_before_live_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def live_info(_url, _timeout):
        return {"id": "x", "is_live": True}

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", live_info)
    update = _FakeUpdate()
    result = asyncio.run(main_pipeline.process_single_video(VIDEO_URL, update))

    assert result is False
    assert update.message.replies[0] == "⏳ Обрабатываю..."
    assert "Live-трансляции не поддерживаются" in update.message.replies[1]


def test_mode_database_and_error_cleanup_failures_stay_contained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def info(_url, _timeout):
        return {"id": "media1", "title": "Sermon", "duration": 600}

    def broken_db():
        raise RuntimeError("mode database unavailable")

    def broken_cleanup(_media_id):
        raise OSError("cleanup unavailable")

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", info)
    monkeypatch.setattr(main_pipeline, "_db_conn", broken_db)
    monkeypatch.setattr(main_pipeline, "lowest_free_gb", lambda *_paths: 50.0)
    monkeypatch.setattr(main_pipeline, "cleanup_files", broken_cleanup)
    _stop_at_delivery_boundary(monkeypatch)
    update = _FakeUpdate()

    assert _run(update) is False
    assert "stop at delivery boundary" in update.message.replies[-1]


def test_current_livedub_file_id_skips_vot_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _eng_environment(monkeypatch, "eng")

    async def cached(_media_id):
        return {"livedub_file_id_version": "v1", "livedub_file_id": "file-id"}

    monkeypatch.setattr(main_pipeline, "adb_get", cached)
    import services.yandex_live_dub as live_dub

    helper_calls = []

    def unexpected_helper():
        helper_calls.append(True)
        return True

    monkeypatch.setattr(live_dub, "_ensure_vot_helper", unexpected_helper)
    _stop_at_delivery_boundary(monkeypatch)
    update = _FakeUpdate()

    assert _run(update) is False
    assert helper_calls == []
    assert "stop at delivery boundary" in update.message.replies[-1]


def test_stale_livedub_file_id_is_not_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _eng_environment(monkeypatch, "eng_fast")

    async def stale(_media_id):
        return {"livedub_file_id_version": "old", "livedub_file_id": "stale-id"}

    monkeypatch.setattr(main_pipeline, "adb_get", stale)
    update = _FakeUpdate()

    assert _run(update) is False
    assert "нет клиента для Яндекс LiveDub" in update.message.replies[0]


def test_livedub_cache_read_failure_uses_normal_vot_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _eng_environment(monkeypatch, "eng_fast")

    async def broken_cache(_media_id):
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr(main_pipeline, "adb_get", broken_cache)
    update = _FakeUpdate()

    assert _run(update) is False
    assert "нет клиента для Яндекс LiveDub" in update.message.replies[0]


def test_eng_full_helper_and_notice_failures_degrade_to_rus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _eng_environment(monkeypatch, "eng")
    import services.yandex_live_dub as live_dub

    def broken_helper():
        raise RuntimeError("helper unavailable")

    async def broken_reply(_text, **_kwargs):
        raise RuntimeError("Telegram unavailable")

    monkeypatch.setattr(live_dub, "_ensure_vot_helper", broken_helper)
    _stop_at_delivery_boundary(monkeypatch)
    update = _FakeUpdate()
    monkeypatch.setattr(update.message, "reply_text", broken_reply)

    # Both user-facing failures are logged and contained; ENG Full degrades to
    # the ordinary RUS path instead of aborting before the delivery boundary.
    assert _run(update) is False


def test_eng_quick_workdir_is_removed_when_settings_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _eng_environment(monkeypatch, "eng_fast")
    import services.yandex_live_dub as live_dub

    async def info(_url, _timeout):
        return {"id": "media-cleanup", "title": "Sermon", "duration": 600}

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", info)
    monkeypatch.setattr(live_dub, "_ensure_vot_helper", lambda: True)
    settings_calls = []

    async def broken_setting(name):
        settings_calls.append(name)
        raise RuntimeError("stop after workdir")

    monkeypatch.setattr(main_pipeline, "asettings_get", broken_setting)
    update = _FakeUpdate()

    assert _run(update) is False
    assert settings_calls == ["livedub_pro_mix"]
    assert "stop after workdir" in update.message.replies[-1]

    import tempfile
    from pathlib import Path

    assert not (Path(tempfile.gettempdir()) / "livedub_media-cleanup").exists()


def test_eng_quick_background_executes_extracted_dub_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real background task enters the extracted Pro-mix/fallback stage."""
    _eng_environment(monkeypatch, "eng_fast")

    import services.livedub_info as livedub_info
    import services.livedub_mix as mix
    import services.yandex_live_dub as live_dub

    started = asyncio.Event()
    calls = []

    async def info(_url, _timeout):
        return {
            "id": "media1",
            "title": "Sermon",
            "duration": 600,
            "language": "ru",
        }

    async def pro(video_url, workdir, *, duration, lang):
        calls.append((video_url, workdir.name, duration, lang))
        output = workdir / "pro.mp4"
        output.write_bytes(b"video")
        started.set()
        return output

    async def stop_after_dub(**_kwargs):
        await asyncio.wait_for(started.wait(), timeout=2)
        raise RuntimeError("stop after extracted dub stage")

    async def setting(name):
        return name == "livedub_pro_mix"

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", info)
    monkeypatch.setattr(main_pipeline, "asettings_get", setting)
    monkeypatch.setattr(main_pipeline, "finish_livedub_quick", stop_after_dub)
    monkeypatch.setattr(live_dub, "_ensure_vot_helper", lambda: True)
    monkeypatch.setattr(livedub_info, "livedub_info_enabled", lambda: False)
    monkeypatch.setattr(mix, "build_pro_dub", pro)
    update = _FakeUpdate()

    assert _run(update) is False
    assert calls == [(VIDEO_URL, "livedub_media1", 600, "ru")]
    assert update.message.replies == ["❌ Ошибка: stop after extracted dub stage"]


def test_eng_quick_background_records_auth_failure_via_extracted_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real background task executes the extracted marker error policy."""
    _eng_environment(monkeypatch, "eng_fast")

    from pathlib import Path

    import services.livedub_info as livedub_info
    import services.livedub_mix as mix
    import services.yandex_live_dub as live_dub

    marker_written = asyncio.Event()
    writes = []
    real_write_text = Path.write_text

    def observed_write(path, content, *args, **kwargs):
        result = real_write_text(path, content, *args, **kwargs)
        if path.name == ".auth_required":
            writes.append((path.name, content))
            marker_written.set()
        return result

    async def pro(*_args, **_kwargs):
        raise RuntimeError("LIVEDUB_AUTH_REQUIRED")

    async def stop_after_marker(**_kwargs):
        await asyncio.wait_for(marker_written.wait(), timeout=2)
        raise RuntimeError("stop after auth marker")

    async def setting(name):
        return name == "livedub_pro_mix"

    monkeypatch.setattr(main_pipeline, "asettings_get", setting)
    monkeypatch.setattr(main_pipeline, "finish_livedub_quick", stop_after_marker)
    monkeypatch.setattr(live_dub, "_ensure_vot_helper", lambda: True)
    monkeypatch.setattr(livedub_info, "livedub_info_enabled", lambda: False)
    monkeypatch.setattr(mix, "build_pro_dub", pro)
    monkeypatch.setattr(Path, "write_text", observed_write)
    update = _FakeUpdate()

    assert _run(update) is False
    assert writes == [(".auth_required", "1")]
    assert update.message.replies == ["❌ Ошибка: stop after auth marker"]


def _audio_too_large_environment(
    monkeypatch: pytest.MonkeyPatch, *, cache_hit: bool
) -> list[str]:
    """Reach fresh/cache audio preparation without network, files, or Telegram."""
    cleaned: list[str] = []

    async def info(_url, _timeout):
        return {"id": "media1", "title": "Sermon", "duration": 600}

    async def no_thumbnail(**_kwargs):
        return None

    async def cache_decision(_media_id):
        return SimpleNamespace(
            hit=cache_hit,
            cached={"telegraph_url": ""} if cache_hit else None,
            ai_data={} if cache_hit else None,
        )

    async def too_large(**_kwargs):
        raise main_pipeline.AudioTooLarge(60.0, 50)

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", info)
    monkeypatch.setattr(main_pipeline, "_db_conn", lambda: _FakeConn(""))
    monkeypatch.setattr(main_pipeline, "lowest_free_gb", lambda *_paths: 50.0)
    monkeypatch.setattr(main_pipeline, "cleanup_files", cleaned.append)
    monkeypatch.setattr(main_pipeline, "load_thumbnail", no_thumbnail)
    monkeypatch.setattr(main_pipeline, "load_cache", cache_decision)
    monkeypatch.setattr(main_pipeline, "prepare_audio", too_large)
    return cleaned


def test_fresh_audio_too_large_executes_shared_early_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleaned = _audio_too_large_environment(monkeypatch, cache_hit=False)
    update = _FakeUpdate()

    assert _run(update) is False
    assert cleaned == ["media1"]
    assert update.message.replies == [str(main_pipeline.AudioTooLarge(60.0, 50))]


def test_cache_audio_too_large_executes_shared_early_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleaned = _audio_too_large_environment(monkeypatch, cache_hit=True)
    update = _FakeUpdate()

    assert _run(update) is False
    assert cleaned == ["media1"]
    assert update.message.replies == [
        "⚠️ Файл слишком большой (60.0 МБ) даже после сжатия."
    ]


def test_cache_hit_delivery_reaches_real_stage_and_returns_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Main wiring executes the extracted stage with the original cached values."""
    from io import BytesIO

    from pipelines.cached_presentation_stage import CachedCaption
    from pipelines.delivery_stage import DeliveryOutcome

    events = []
    audio_path = tmp_path / "cached.mp3"
    thumbnail = BytesIO(b"thumbnail")
    cached_ai = {"format": "lecture", "real_title": "Cached lecture"}
    cached = {
        "telegraph_url": "cached-synopsis",
        "quotes_tg_url": "cached-quotes",
        "questions_tg_url": "cached-questions",
        "terms_tg_url": "cached-terms",
        "study_tg_url": "cached-study",
        "reflection_tg_url": "cached-reflection",
        "rutube_url": "cached-rutube",
        "vk_url": "cached-vk",
        "audio_file_id": "cached-file-id",
    }

    async def info(_url, _timeout):
        return {
            "id": "media1",
            "title": "Cached sermon",
            "channel": "Cached channel",
            "duration": 600,
        }

    async def cache_hit(_media_id):
        return SimpleNamespace(hit=True, cached=cached, ai_data=cached_ai)

    async def load_thumbnail(**_kwargs):
        return thumbnail

    async def audio_ready(**_kwargs):
        return SimpleNamespace(path=audio_path, size_mb=1.0, bitrate="128k")

    async def present(request):
        events.append(("presentation", request))
        return CachedCaption(caption="cached caption", full_caption="full cached caption")

    async def deliver(request):
        events.append(("delivery", request))
        return DeliveryOutcome(
            chapters_embedded=False,
            audio_message=None,
            full_caption_sent=True,
            pdf_status="skipped",
            quiz_sent=0,
        )

    def cleanup(media_id):
        events.append(("cleanup", media_id))

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", info)
    monkeypatch.setattr(main_pipeline, "_db_conn", lambda: _FakeConn(""))
    monkeypatch.setattr(main_pipeline, "lowest_free_gb", lambda *_paths: 50.0)
    monkeypatch.setattr(main_pipeline, "cleanup_files", cleanup)
    monkeypatch.setattr(main_pipeline, "load_cache", cache_hit)
    monkeypatch.setattr(main_pipeline, "load_thumbnail", load_thumbnail)
    monkeypatch.setattr(main_pipeline, "prepare_audio", audio_ready)
    monkeypatch.setattr(main_pipeline, "prepare_cached_presentation", present)
    monkeypatch.setattr(main_pipeline, "deliver_cached_result", deliver)
    update = _FakeUpdate()

    assert _run(update) is True
    assert update.message.replies == []
    assert [event[0] for event in events] == ["presentation", "delivery", "cleanup"]
    assert events[-1] == ("cleanup", "media1")

    presentation = events[0][1]
    assert presentation.ai_data is cached_ai
    assert presentation.full_title == "Cached sermon"
    assert presentation.channel_name == "Cached channel"
    assert presentation.duration == 600
    assert presentation.file_size_mb == 1.0
    assert presentation.bitrate == "128k"
    assert presentation.cached_rutube_url == "cached-rutube"
    assert presentation.cached_vk_url == "cached-vk"
    assert presentation.links.synopsis_url == "cached-synopsis"
    assert presentation.links.quotes_url == "cached-quotes"
    assert presentation.links.questions_url == "cached-questions"
    assert presentation.links.terms_url == ""
    assert presentation.links.study_url == "cached-study"
    assert presentation.links.reflection_url == "cached-reflection"

    delivery = events[1][1]
    assert delivery.update is update
    assert delivery.context is None
    assert delivery.mp3_path == audio_path
    assert delivery.media_id == "media1"
    assert delivery.caption == "cached caption"
    assert delivery.full_caption == "full cached caption"
    assert delivery.ai_data is cached_ai
    assert delivery.thumbnail is thumbnail
    assert delivery.cached_file_id == "cached-file-id"
    assert delivery.synopsis_url == "cached-synopsis"
    assert delivery.study_url == "cached-study"
    assert delivery.reflection_url == "cached-reflection"
    assert delivery.terms_url == ""
    assert delivery.cache_hit is False


@pytest.mark.parametrize(
    "mode",
    [
        "",
        "eng",
        "eng_timeout",
        "eng_send_failure",
        "eng_video_send_failure",
        "eng_video_cover_retry_failure",
        "eng_video_too_large",
    ],
)
def test_fresh_caption_finalization_reaches_delivery_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path, mode: str
) -> None:
    """Fresh wiring reaches delivery; ENG variants execute exception notices."""
    cleaned: list[str] = []
    analysis_requests = []
    publication_calls = []
    render_requests = []
    navigation_calls = []
    repair_calls = []
    fit_calls = []
    save_calls = []
    archive_calls = []
    delivery_requests = []
    prompt_calls = []
    audio_path = tmp_path / "fresh.mp3"
    questions = {"blocks": [{"text": "Question"}]}
    terms_data = {
        "concepts": ["concept"],
        "scripture": [],
        "translations": [],
        "lexicon_notes": [],
    }
    ai_data = {
        "real_title": "Fresh lecture title with enough detail",
        "real_author": "John Smith",
        "questions": questions,
        "terms_data": terms_data,
        "format": "lecture",
        "timestamps": "00:00 Intro\n00:10 Topic",
    }
    settings = {
        "analytics": True,
        "questions": True,
        "terms": True,
        "study_analysis": True,
        "reflection_application": False,
    }
    settings_calls = []

    async def info(_url, _timeout):
        return {
            "id": "media1",
            "title": "Original fallback title",
            "channel": "Channel",
            "duration": 600,
        }

    async def no_thumbnail(**_kwargs):
        return None

    async def cache_miss(_media_id):
        return SimpleNamespace(hit=False, cached=None, ai_data=None)

    async def audio_ready(**_kwargs):
        return SimpleNamespace(path=audio_path, size_mb=1.0, bitrate="128k")

    async def analysis_ready(request, *, enabled):
        analysis_requests.append((request, enabled))
        return SimpleNamespace(
            ai_data=ai_data,
            telegraph_url="https://telegra.ph/synopsis",
            synopsis_outline="outline",
            used_client=None,
            used_audio_part=None,
            degraded_caption_data=None,
            early_links=None,
            rutube_url="",
            vk_url="",
        )

    async def batch_settings():
        settings_calls.append(True)
        return settings

    real_prepare = main_pipeline.prepare_publication_context

    async def capture_publication(**kwargs):
        context = await real_prepare(**kwargs)
        publication_calls.append((kwargs, context))
        return context

    from pipelines.caption_stage import CaptionFit
    from pipelines.telegraph_render import RenderedPages
    from pipelines.telegraph_repair_stage import RepairReport

    repair_report = RepairReport()

    async def render_ready(request):
        render_requests.append(request)
        return RenderedPages(
            questions_url="questions",
            terms_url="terms",
            study_analysis_url="study",
            alt_links={"rutube": "alt-rutube", "vk": "alt-vk"},
        )

    async def navigation_ready(pages):
        navigation_calls.append(pages)

    async def repair_ready(urls, *, enabled):
        repair_calls.append((urls, enabled))
        return repair_report

    def fit_ready(source, caption_base):
        fit_calls.append((source, caption_base))
        return CaptionFit(
            caption="fitted caption",
            full_caption="full fitted caption",
            timestamps_total=2,
            timestamps_shown=1,
            trim_stage="timestamps_trimmed",
            data={"fitted": True},
        )

    async def save_ready(**kwargs):
        save_calls.append(kwargs)

    async def archive_ready(request, *, repair):
        archive_calls.append((request, repair))

    def prompt_ready():
        prompt_calls.append(True)
        return "prompt-test"

    async def stop_at_delivery(request):
        delivery_requests.append(request)
        raise RuntimeError("stop at delivery boundary")

    notice_bot = None
    context = None
    if mode.startswith("eng"):
        import services.yandex_live_dub as live_dub

        async def no_cache(_media_id):
            return None

        async def no_optional_setting(_name):
            return False

        def completed_livedub_task(coroutine):
            assert coroutine.cr_code.co_name == "_run_livedub_bg"
            coroutine.close()
            task = asyncio.get_running_loop().create_future()
            if mode == "eng_timeout":
                task.set_exception(asyncio.TimeoutError())
            elif mode == "eng_send_failure":
                task.set_exception(RuntimeError("Request Entity Too Large <bad>"))
            elif mode in (
                "eng_video_send_failure",
                "eng_video_cover_retry_failure",
                "eng_video_too_large",
            ):
                translated = tmp_path / "translated.mp4"
                translated.write_bytes(b"video")
                task.set_result((translated, False, False))
            else:
                task.set_result(None)
            return task

        class NoticeBot:
            def __init__(self):
                self.messages = []
                self.video_calls = []
                self.actions = []

            async def send_message(self, **kwargs):
                self.messages.append(kwargs)

            async def send_chat_action(self, **kwargs):
                self.actions.append(kwargs)

            async def send_video(self, **kwargs):
                self.video_calls.append(kwargs)
                if mode == "eng_video_cover_retry_failure":
                    if "cover" in kwargs:
                        raise RuntimeError("unknown cover field")
                    raise RuntimeError("fallback send failed")
                raise RuntimeError("Request Entity Too Large <bad>")

        monkeypatch.setattr(main_pipeline, "adb_get", no_cache)
        monkeypatch.setattr(main_pipeline, "asettings_get", no_optional_setting)
        monkeypatch.setattr(main_pipeline.asyncio, "create_task", completed_livedub_task)
        monkeypatch.setattr(live_dub, "_ensure_vot_helper", lambda: True)
        if mode in (
            "eng_send_failure",
            "eng_video_send_failure",
            "eng_video_cover_retry_failure",
        ):
            monkeypatch.setattr(main_pipeline, "get_max_file_size_mb", lambda: 50)
        elif mode == "eng_video_too_large":
            monkeypatch.setattr(main_pipeline, "get_max_file_size_mb", lambda: 0)
        if mode in (
            "eng_video_send_failure",
            "eng_video_cover_retry_failure",
            "eng_video_too_large",
        ):
            import services.livedub_qa as qa

            monkeypatch.setattr(
                qa, "technical_check", lambda *_args: ["tech warning"]
            )
        if mode in ("eng_video_send_failure", "eng_video_cover_retry_failure"):
            import services.livedub_mix as mix

            thumbs_dir = tmp_path / "thumbs"
            thumbs_dir.mkdir()
            if mode == "eng_video_cover_retry_failure":
                (thumbs_dir / "media1_thumb.jpg").write_bytes(b"x" * 2048)
            monkeypatch.setattr(main_pipeline, "THUMBS_DIR", thumbs_dir)
            monkeypatch.setattr(
                mix,
                "probe_video_meta",
                lambda _path: {"width": 640, "height": 360, "duration": 12.5},
            )
            monkeypatch.setattr(mix, "make_video_thumbnail", lambda _path: None)
        notice_bot = NoticeBot()
        context = SimpleNamespace(bot=notice_bot)

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", info)
    monkeypatch.setattr(main_pipeline, "_db_conn", lambda: _FakeConn("eng" if mode.startswith("eng") else mode))
    monkeypatch.setattr(main_pipeline, "lowest_free_gb", lambda *_paths: 50.0)
    monkeypatch.setattr(main_pipeline, "cleanup_files", cleaned.append)
    monkeypatch.setattr(main_pipeline, "load_thumbnail", no_thumbnail)
    monkeypatch.setattr(main_pipeline, "load_cache", cache_miss)
    monkeypatch.setattr(main_pipeline, "prepare_audio", audio_ready)
    monkeypatch.setattr(main_pipeline, "run_analysis", analysis_ready)
    monkeypatch.setattr(main_pipeline, "asettings_get_all", batch_settings)
    monkeypatch.setattr(
        main_pipeline, "prepare_publication_context", capture_publication
    )
    monkeypatch.setattr(main_pipeline, "render_pages", render_ready)
    monkeypatch.setattr(main_pipeline, "add_navigation", navigation_ready)
    monkeypatch.setattr(main_pipeline, "repair_pages", repair_ready)
    monkeypatch.setattr(main_pipeline, "fit_caption", fit_ready)
    monkeypatch.setattr(main_pipeline, "adb_save", save_ready)
    monkeypatch.setattr(main_pipeline, "archive_pages", archive_ready)
    monkeypatch.setattr(main_pipeline, "get_prompt_fingerprint", prompt_ready)
    monkeypatch.setattr(main_pipeline, "deliver_result", stop_at_delivery)
    monkeypatch.setenv("TELEGRAPH_AUTO_REPAIR_AFTER_PUBLISH", "1")
    monkeypatch.setenv("PROMPT_EXPERIMENT_TAG", "integration")
    update = _FakeUpdate()
    update.effective_chat = SimpleNamespace(id=987)
    update.message.message_id = 777

    assert _run(update, context=context) is False
    assert update.message.replies == ["❌ Ошибка: stop at delivery boundary"]
    assert cleaned == ["media1"]
    if mode.startswith("eng"):
        assert notice_bot is not None
        assert len(notice_bot.messages) == 1
        notice = notice_bot.messages[0]
        assert notice["chat_id"] == 987
        assert notice["reply_to_message_id"] == update.message.message_id
        if mode == "eng":
            assert "Живой перевод Яндекса не получился" in notice["text"]
            assert "Яндекс/VOT не отдал живой дубляж" in notice["text"]
        elif mode == "eng_timeout":
            assert "не успел за 30 минут" in notice["text"]
        elif mode == "eng_video_too_large":
            assert "Видео с переводом готово, но оно весит" in notice["text"]
            assert "Текущий лимит отправки — 0 МБ." in notice["text"]
            assert "LOCAL_BOT_API_URL" in notice["text"]
            assert "parse_mode" not in notice
        elif mode == "eng_video_cover_retry_failure":
            assert "Не удалось отправить видео с переводом" in notice["text"]
            assert "fallback send failed" in notice["text"]
            assert "LOCAL_BOT_API_URL" not in notice["text"]
        else:
            assert "Не удалось отправить видео с переводом" in notice["text"]
            assert "Request Entity Too Large &lt;bad&gt;" in notice["text"]
            assert "LOCAL_BOT_API_URL" in notice["text"]
        if mode in ("eng_video_send_failure", "eng_video_cover_retry_failure"):
            assert notice_bot.actions == [
                {"chat_id": 987, "action": "upload_video"}
            ]
            expected_calls = 2 if mode == "eng_video_cover_retry_failure" else 1
            assert len(notice_bot.video_calls) == expected_calls
            video_call = notice_bot.video_calls[0]
            assert video_call["width"] == 640
            assert video_call["height"] == 360
            assert video_call["duration"] == 12.5
            assert video_call["supports_streaming"] is True
            assert "⚠️ tech warning" in video_call["caption"]
            if mode == "eng_video_cover_retry_failure":
                assert video_call["cover"].name == "media1_thumb.jpg"
                assert "cover" not in notice_bot.video_calls[1]
            else:
                assert "cover" not in video_call
        else:
            assert notice_bot.video_calls == []
    else:
        assert notice_bot is None
    assert settings_calls == [True]
    assert len(analysis_requests) == 1
    assert analysis_requests[0][0].mp3_path == audio_path
    assert len(publication_calls) == 1

    publication_kwargs, publication = publication_calls[0]
    assert publication_kwargs["ai_data"] is ai_data
    assert publication_kwargs["degraded"] is None
    assert publication_kwargs["full_title"] == "Original fallback title"
    assert publication_kwargs["media_id"] == "media1"
    assert publication_kwargs["telegraph_url"] == "https://telegra.ph/synopsis"
    assert publication.search_title == (
        "Fresh lecture title with enough detail - John Smith"
    )
    assert publication.tg_author == "John Smith"
    assert publication.questions is questions
    assert publication.terms_data is terms_data
    assert publication.format_name == "lecture"
    assert publication.timestamps_total == 2
    assert publication.plan.analytics is False
    assert publication.plan.questions is True
    assert publication.plan.terms is True
    assert publication.plan.study_analysis is True
    assert publication.plan.reflection_application is False

    assert len(render_requests) == 1
    render = render_requests[0]
    assert render.ai_data is ai_data
    assert render.questions is questions
    assert render.terms_data is terms_data
    assert render.plan is publication.plan
    assert render.search_title == publication.search_title
    assert render.author == publication.tg_author
    assert render.full_title == "Original fallback title"
    assert render.synopsis_outline == "outline"
    assert navigation_calls == [[
        ("Конспект", "https://telegra.ph/synopsis"),
        ("Разбор материала", "study"),
    ]]
    assert repair_calls == [(
        [
            "https://telegra.ph/synopsis",
            "study",
            None,
            None,
            "questions",
            "terms",
        ],
        True,
    )]
    assert len(fit_calls) == 1
    caption_source, caption_base = fit_calls[0]
    assert caption_source.format_name == "lecture"
    assert caption_source.quotes_link == "study"
    assert caption_source.questions_link == "questions"
    assert caption_source.terms_link == "terms"
    assert caption_base["real_title"] == ai_data["real_title"]

    assert len(save_calls) == 1
    saved = save_calls[0]
    assert saved["video_id"] == "media1"
    assert saved["ai_data"]["caption_trim_stage"] == "timestamps_trimmed"
    assert saved["ai_data"]["caption_timestamps_total"] == 2
    assert saved["ai_data"]["caption_timestamps_shown"] == 1
    assert saved["ai_data"]["duration"] == 600
    assert saved["quotes_tg_url"] == "study"
    assert saved["questions_tg_url"] == "questions"
    assert saved["terms_tg_url"] == "terms"
    assert saved["rutube_url"] == "alt-rutube"
    assert saved["vk_url"] == "alt-vk"
    assert saved["prompt_version"] == "prompt-test"
    assert saved["publication_status"] == "complete"
    assert saved["publication_missing"] == "[]"

    assert len(archive_calls) == 1
    archive_request, archived_repair = archive_calls[0]
    assert archived_repair is repair_report
    assert archive_request.ai_data is saved["ai_data"]
    assert archive_request.search_title == publication.search_title
    assert archive_request.author == publication.tg_author
    assert archive_request.format_name == "lecture"
    assert archive_request.duration == 600
    assert archive_request.rutube_url == "alt-rutube"
    assert archive_request.vk_url == "alt-vk"
    assert archive_request.study_url == "study"
    assert archive_request.reflection_url == ""
    assert archive_request.terms_url == "terms"
    assert archive_request.questions_url == "questions"
    assert archive_request.prompt_version == "prompt-test"
    assert archive_request.prompt_variant == "integration"
    assert prompt_calls == [True, True]

    assert len(delivery_requests) == 1
    delivery = delivery_requests[0]
    assert delivery.mp3_path == audio_path
    assert delivery.caption == "fitted caption"
    assert delivery.full_caption == "full fitted caption"
    assert delivery.ai_data is saved["ai_data"]
    assert delivery.synopsis_url == "https://telegra.ph/synopsis"
    assert delivery.study_url == "study"
    assert delivery.reflection_url == ""
    assert delivery.terms_url == "terms"
