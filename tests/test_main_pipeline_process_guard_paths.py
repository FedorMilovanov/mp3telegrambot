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


def _run(update: _FakeUpdate, *, silent_errors: bool = False) -> bool:
    return asyncio.run(
        main_pipeline.process_single_video(
            VIDEO_URL,
            update,
            status_msg=_FakeStatus(),
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



def test_fresh_publication_context_reaches_render_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Fresh analysis reaches rendering with the exact extracted context."""
    cleaned: list[str] = []
    analysis_requests = []
    publication_calls = []
    render_requests = []
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

    async def stop_at_render(request):
        render_requests.append(request)
        raise RuntimeError("stop at render boundary")

    monkeypatch.setattr(main_pipeline, "_ytdlp_info_inprocess", info)
    monkeypatch.setattr(main_pipeline, "_db_conn", lambda: _FakeConn(""))
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
    monkeypatch.setattr(main_pipeline, "render_pages", stop_at_render)
    update = _FakeUpdate()

    assert _run(update) is False
    assert update.message.replies == ["❌ Ошибка: stop at render boundary"]
    assert cleaned == ["media1"]
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
