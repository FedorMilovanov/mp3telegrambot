#!/usr/bin/env python3
"""Деградированный режим: что получает пользователь без Gemini.

Раньше этот код проверялся только тогда, когда у бота кончалась квота, — то
есть в худший возможный момент. Самое важное здесь: пометка в подписи честная,
таймкоды добавляются без Gemini, а любая проблема с субтитрами не превращает
деградацию в ошибку отправки.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pipelines import degraded_mode


def _attach(tmp_path: Path, **overrides):
    kwargs = {
        "url": "https://youtu.be/abc",
        "media_id": "abc",
        "download_dir": tmp_path,
        "duration": 600,
        "source_lang": "ru",
    }
    kwargs.update(overrides)
    data = kwargs.pop("data", degraded_mode.degraded_caption_data("Название", "Автор"))
    return asyncio.run(degraded_mode.attach_transcript_timestamps(data, **kwargs))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DEGRADED_TRANSCRIPT_TIMESTAMPS", raising=False)


# ── данные подписи ──────────────────────────────────────────────────────────


def test_caption_keeps_format_compatible_with_full_analysis() -> None:
    """Поля те же, что у полного анализа: вызывающий код не разветвляется."""
    data = degraded_mode.degraded_caption_data("Название", "Автор")
    assert data["real_title"] == "Название"
    assert data["real_author"] == "Автор"
    assert data["format"] == "other"


def test_caption_warns_the_user_honestly() -> None:
    data = degraded_mode.degraded_caption_data("Н", "А")
    warning = data["_ai_unavailable_warning"]
    assert "недоступен" in warning and "квота" in warning
    assert "MP3" in warning


# ── выключатель ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "", None])
def test_fallback_is_enabled_by_default(value) -> None:
    assert degraded_mode.transcript_fallback_enabled(value) is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", " OFF "])
def test_fallback_can_be_disabled(value) -> None:
    assert degraded_mode.transcript_fallback_enabled(value) is False


# ── таймкоды ────────────────────────────────────────────────────────────────


def test_timestamps_are_added_without_gemini(tmp_path) -> None:
    calls: list[dict] = []

    async def fetch(url, work_dir, **kwargs):
        calls.append({"url": url, "work_dir": Path(work_dir), **kwargs})
        return "00:00 текст"

    def to_timestamps(text, max_lines):
        assert max_lines == degraded_mode.TRANSCRIPT_MAX_LINES
        return "00:00 — текст"

    data = _attach(tmp_path, fetch=fetch, to_timestamps=to_timestamps)

    assert data["timestamps"] == "00:00 — текст"
    assert data["_timestamps_source"] == "youtube_transcript"
    assert calls[0]["max_chars"] == degraded_mode.TRANSCRIPT_MAX_CHARS
    assert calls[0]["expected_duration"] == 600
    assert calls[0]["lang"] == "ru"


def test_empty_language_falls_back_to_russian(tmp_path) -> None:
    seen: list[str] = []

    async def fetch(_url, _dir, **kwargs):
        seen.append(kwargs["lang"])
        return "текст"

    _attach(
        tmp_path,
        source_lang="",
        fetch=fetch,
        to_timestamps=lambda _t, max_lines: "00:00 — текст",
    )
    assert seen == ["ru"]


def test_no_timestamps_keeps_data_untouched(tmp_path) -> None:
    data = degraded_mode.degraded_caption_data("Н", "А")

    async def fetch(_url, _dir, **_kwargs):
        return ""

    result = _attach(tmp_path, data=data, fetch=fetch, to_timestamps=lambda _t, max_lines: "")
    assert result == data
    assert "timestamps" not in result


def test_fetch_failure_does_not_break_delivery(tmp_path) -> None:
    """Субтитров может не быть — это не повод не отправить mp3."""
    data = degraded_mode.degraded_caption_data("Н", "А")

    async def fetch(_url, _dir, **_kwargs):
        raise RuntimeError("субтитры недоступны")

    assert _attach(tmp_path, data=data, fetch=fetch, to_timestamps=lambda t, max_lines: t) == data


def test_switch_off_skips_fetch(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEGRADED_TRANSCRIPT_TIMESTAMPS", "0")

    async def fetch(*_a, **_k):
        raise AssertionError("запрос субтитров должен быть пропущен")

    data = degraded_mode.degraded_caption_data("Н", "А")
    assert _attach(tmp_path, data=data, fetch=fetch, to_timestamps=lambda t, _m: t) == data


def test_work_directory_is_created_and_removed(tmp_path) -> None:
    """Деградация не должна оставлять мусор в каталоге загрузок."""
    seen: list[Path] = []

    async def fetch(_url, work_dir, **_kwargs):
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "sub.srt").write_text("1", encoding="utf-8")
        seen.append(work_dir)
        return "текст"

    _attach(tmp_path, fetch=fetch, to_timestamps=lambda _t, max_lines: "00:00 — t")
    assert seen and not seen[0].exists()


def test_work_directory_is_removed_even_on_failure(tmp_path) -> None:
    seen: list[Path] = []

    async def fetch(_url, work_dir, **_kwargs):
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        seen.append(work_dir)
        raise RuntimeError("сеть упала")

    _attach(
        tmp_path, data=degraded_mode.degraded_caption_data("Н", "А"), fetch=fetch, to_timestamps=lambda t, max_lines: t
    )
    assert seen and not seen[0].exists()


def test_original_dict_is_not_mutated(tmp_path) -> None:
    """Частичный результат не должен протекать в вызывающий код."""
    data = degraded_mode.degraded_caption_data("Н", "А")

    async def fetch(_url, _dir, **_kwargs):
        return "текст"

    result = _attach(tmp_path, data=data, fetch=fetch, to_timestamps=lambda _t, max_lines: "00:00")
    assert "timestamps" not in data
    assert "timestamps" in result


def test_default_sources_are_the_youtube_transcript_helpers(tmp_path, monkeypatch) -> None:
    """Без явных аргументов берутся штатные функции, а не заглушки."""
    import services.youtube_transcript as yt

    calls: list[str] = []

    async def fake_download(url, work_dir, **kwargs):
        calls.append(f"download:{url}:{kwargs['lang']}")
        return "00:00 текст"

    def fake_convert(text, max_lines):
        calls.append(f"convert:{max_lines}")
        return "00:00 — текст"

    monkeypatch.setattr(yt, "download_youtube_transcript_text", fake_download)
    monkeypatch.setattr(yt, "timed_text_to_caption_timestamps", fake_convert)

    data = _attach(tmp_path)
    assert data["timestamps"] == "00:00 — текст"
    assert calls == ["download:https://youtu.be/abc:ru", f"convert:{degraded_mode.TRANSCRIPT_MAX_LINES}"]
