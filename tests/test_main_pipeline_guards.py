#!/usr/bin/env python3
"""Гварды пайплайна и детерминированные помощники `main_pipeline`.

`process_single_video` — 2600 строк, и до этих тестов единственным способом
проверить отказ «live-трансляция» или «видео длиннее трёх часов» был живой бот.
Тексты отказов видит пользователь, поэтому они зафиксированы дословно: правка
формулировки должна менять и тест, а не проходить незаметно.
"""

from __future__ import annotations

import pytest

from pipelines import livedub_title, main_pipeline, media_guards as guards


# ── live-трансляции ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "info",
    [
        {"is_live": True},
        {"live_status": "is_live"},
        {"live_status": "is_upcoming"},
        {"live_status": "post_live"},
    ],
)
def test_live_streams_are_refused(info: dict) -> None:
    skip = guards.live_skip(info)
    assert skip is not None
    assert skip.reason == "live-stream"
    assert "Live-трансляции не поддерживаются" in skip.message


@pytest.mark.parametrize(
    "info",
    [
        None,
        {},
        {"is_live": False, "live_status": "not_live"},
        {"live_status": "was_live"},  # запись завершённой трансляции — можно
        "не словарь",
    ],
)
def test_finished_streams_are_processed(info) -> None:
    assert guards.live_skip(info) is None


# ── длительность ────────────────────────────────────────────────────────────


def test_long_video_is_refused_with_readable_minutes() -> None:
    skip = guards.duration_skip(3 * 3600 + 1, 3 * 3600)
    assert skip is not None
    assert skip.reason == "too-long"
    assert "180 мин" in skip.message
    assert "лимит 180 мин" in skip.message
    assert skip.details == f"{3 * 3600 + 1}s > {3 * 3600}s"


@pytest.mark.parametrize("duration", [None, 0, -5, 3600, "не число"])
def test_unknown_or_short_duration_is_not_a_refusal(duration) -> None:
    """`duration` не всегда есть в метаданных: отказ на пустом месте хуже попытки."""
    assert guards.duration_skip(duration, 3600) is None


def test_duration_boundary_is_inclusive() -> None:
    assert guards.duration_skip(3600, 3600) is None
    assert guards.duration_skip(3601, 3600) is not None


# ── диск ────────────────────────────────────────────────────────────────────


def test_low_disk_is_refused_with_size() -> None:
    skip = guards.disk_skip(1.44)
    assert skip is not None
    assert skip.reason == "disk-low"
    assert "1.4 ГБ свободно" in skip.message
    assert skip.details == "1.4 GB < 2.0 GB"


def test_unknown_disk_is_not_a_refusal() -> None:
    assert guards.disk_skip(None) is None


def test_disk_boundary() -> None:
    assert guards.disk_skip(2.0) is None
    assert guards.disk_skip(2.0, min_gb=2.0) is None
    assert guards.disk_skip(1.999) is not None


def test_free_gb_handles_unusable_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    def boom(_path):
        raise OSError("device not ready")

    monkeypatch.setattr(shutil, "disk_usage", boom)
    assert guards.free_gb("/nowhere") is None
    assert guards.lowest_free_gb("/nowhere", "/also-nowhere") is None


def test_lowest_free_gb_takes_the_worst_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """`%TEMP%` и `DOWNLOAD_DIR` могут быть на разных дисках."""
    import shutil as shutil_mod
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")

    def fake_disk_usage(path):
        return usage(total=100, used=0, free=50 * 1024**3 if path == "/big" else 1024**3)

    monkeypatch.setattr(shutil_mod, "disk_usage", fake_disk_usage)
    assert guards.lowest_free_gb("/big", "/small") == pytest.approx(1.0)
    assert guards.lowest_free_gb("/big") == pytest.approx(50.0)


# ── таймауты и числа из окружения ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, 180),
        ("", 180),
        ("30", 60),  # ниже нижней границы
        ("5000", 900),  # выше верхней границы
        ("300", 300),
        ("много", 180),
    ],
)
def test_info_timeout_is_clamped(raw, expected) -> None:
    assert guards.info_timeout_seconds(raw) == expected


def test_info_timeout_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YTDLP_INFO_TIMEOUT", "240")
    assert guards.info_timeout_seconds() == 240


def test_env_int_falls_back_on_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    """`int(os.getenv(...))` в пайплайне падал ValueError на значении «3 часа»."""
    monkeypatch.setenv("MAX_VIDEO_DURATION_SEC", "3 часа")
    assert guards.env_int("MAX_VIDEO_DURATION_SEC", 10800) == 10800


def test_env_int_respects_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_VIDEO_DURATION_SEC", "10")
    assert guards.env_int("MAX_VIDEO_DURATION_SEC", 10800, minimum=60) == 10800
    monkeypatch.setenv("MAX_VIDEO_DURATION_SEC", "600")
    assert guards.env_int("MAX_VIDEO_DURATION_SEC", 10800, minimum=60) == 600


def test_env_int_unset_returns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_VIDEO_DURATION_SEC", raising=False)
    assert guards.env_int("MAX_VIDEO_DURATION_SEC", 10800) == 10800


# ── метаданные yt-dlp ───────────────────────────────────────────────────────


def test_playlist_container_is_reduced_to_first_entry() -> None:
    info = {"entries": [{}, {"id": "second"}, None]}
    assert guards.first_entry(info) == {"id": "second"}


def test_playlist_container_without_entries_is_returned_as_is() -> None:
    """Пустой контейнер — это «метаданных нет», а не «видео не найдено»."""
    info = {"entries": []}
    assert guards.first_entry(info) is info


def test_first_entry_ignores_non_dict() -> None:
    assert guards.first_entry(None) is None
    assert guards.first_entry("строка") is None


def test_video_info_is_returned_unchanged() -> None:
    info = {"id": "abc", "entries": [{"id": "other"}]}
    assert guards.first_entry(info) is info


def test_stdout_parsing_skips_noise() -> None:
    stdout = 'WARNING: плагин\n\n{"id": "abc", "title": "t"}\nещё мусор\n'
    assert guards.parse_info_stdout(stdout) == {"id": "abc", "title": "t"}


def test_stdout_parsing_skips_broken_json() -> None:
    stdout = '{"broken": \n{"id": "ok"}\n'
    assert guards.parse_info_stdout(stdout) == {"id": "ok"}


def test_stdout_parsing_of_empty_output() -> None:
    assert guards.parse_info_stdout("") is None
    assert guards.parse_info_stdout(None) is None


# ── помощники main_pipeline ────────────────────────────────────────────────


def test_livedub_title_compatibility_reexports_match_owner_module() -> None:
    names = (
        "_LIVEDUB_TITLE_CACHE",
        "_LIVEDUB_TITLE_CACHE_MAX",
        "_clean_livedub_meta_text",
        "_replace_known_author_names",
        "_split_title_author_line",
        "_title_part_has_cyrillic",
        "_split_livedub_title_author",
        "_fallback_livedub_ru_title",
        "_format_livedub_title_line",
    )
    for name in names:
        assert getattr(main_pipeline, name) is getattr(livedub_title, name)


def test_clean_meta_text_removes_shorts_markers() -> None:
    cleaned = main_pipeline._clean_livedub_meta_text("#Shorts Проповедь о вере | YouTube Shorts")
    assert cleaned == "Проповедь о вере"


def test_clean_meta_text_of_empty_input() -> None:
    assert main_pipeline._clean_livedub_meta_text(None) == ""
    assert main_pipeline._clean_livedub_meta_text("   —  ") == ""


def test_author_first_line_is_reordered_to_title_first() -> None:
    """YouTube часто даёт «Автор: Название», caption должен быть «Название - Автор»."""
    title, author = main_pipeline._split_title_author_line("John MacArthur: The Gospel")
    assert "The Gospel" in title
    assert "МакАртур" in author


def test_known_author_from_right_side() -> None:
    title, author = main_pipeline._split_title_author_line("True Faith - Paul Washer")
    assert title == "True Faith"
    assert author == "Пол Вошер"


def test_line_without_author_keeps_title_only() -> None:
    title, author = main_pipeline._split_title_author_line("Просто название")
    assert title == "Просто название"
    assert author == ""


def test_cyrillic_detection_uses_title_part_only() -> None:
    """Кириллица только в подставленном авторе не делает название русским."""
    assert main_pipeline._title_part_has_cyrillic("Русское название") is True
    assert main_pipeline._title_part_has_cyrillic("English title - Paul Washer") is False
    # Автор в строке отделяется, поэтому кириллица в нём не считается:
    # название всё ещё английское, и его нужно переводить.
    assert main_pipeline._title_part_has_cyrillic("English title - Пол Вошер") is False


def test_title_line_combines_title_and_author() -> None:
    line = main_pipeline._format_livedub_title_line("Проповедь о вере", "Джон МакАртур")
    # title_case_fragment приводит название к заголовочному регистру —
    # это осознанное поведение, а не побочный эффект.
    assert line == "Проповедь о Вере - Джон МакАртур"


def test_title_line_does_not_duplicate_author() -> None:
    line = main_pipeline._format_livedub_title_line("Джон МакАртур: Проповедь о вере", "Джон МакАртур")
    assert line.count("МакАртур") == 1


def test_title_line_without_both_parts_has_a_fallback() -> None:
    assert main_pipeline._format_livedub_title_line("", "") == "Переведённое видео"


def test_sponsorblock_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPONSORBLOCK_REMOVE", raising=False)
    assert main_pipeline._sponsorblock_args() == []


def test_sponsorblock_valid_categories(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPONSORBLOCK_REMOVE", "sponsor, selfpromo")
    assert main_pipeline._sponsorblock_args() == [
        "--sponsorblock-remove",
        "sponsor,selfpromo",
    ]


def test_sponsorblock_rejects_unknown_category(monkeypatch: pytest.MonkeyPatch) -> None:
    """Опечатка не должна молча вырезать не то."""
    monkeypatch.setenv("SPONSORBLOCK_REMOVE", "sponsor,adverts")
    assert main_pipeline._sponsorblock_args() == []
