"""Regression checks for direct-dubbing agent handoff and YouTube copy rules."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "docs" / "agent_direct_dubbing"


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_route() -> None:
    guide = read("README.md")
    assert "RUSSIAN_NAMING_AND_DELIVERY.md" in guide
    assert "YOUTUBE_PRESENTATION_BY_SURFACE.md" in guide
    assert "APJ1159_2026-11-08_EDITORIAL_LOCK_CASE.md" in guide


def test_delivery() -> None:
    guide = read("RUSSIAN_NAMING_AND_DELIVERY.md")
    assert "Джон Пайпер - Предопределяет ли Бог Людей к Аду - Спроси Пастора Джона №450.mp4" in guide
    assert "01 - Автор - Тема Выпуска.mp4" in guide
    assert "ASCII U+002D" in guide and "TXT" in guide
    assert "локальной передаче" in guide


def test_one_and_ten() -> None:
    guide = read("README.md")
    assert "Переведи и положи 1 видео" in guide
    assert "Переведи и положи 10 видео" in guide
    assert "Не генерировать TXT" in guide
    assert "LOCAL_HANDOFF_READY" in guide and "RIGHTS_APPROVED" in guide


def test_clock_patch_and_existing_apj_guard() -> None:
    guide = read("YANDEX_TRANSLATION_EDITORIAL.md")
    assert "сначала попробовать вырезать только ошибочные слова" in guide
    assert "часов" in guide and "минут" in guide
    assert "## Обязательный STOP-GATE" in guide


def test_channel_and_source_safety() -> None:
    guide = read("YOUTUBE_PRESENTATION_BY_SURFACE.md")
    for required in (
        "*важные слова*",
        "_выделенные слова_",
        "*VK:*",
        "Research/AGENT_RULES.md",
        "gb-is-my-strength/docs/EDITORIAL-SOURCE-POLICY.md",
        "TheLegendaryPoet/docs/EDITORIAL_JUDGMENT_AND_SOURCE_POLICY.md",
        "5000 БАЙТ",
        "PR #453/#455/#456",
    ):
        assert required in guide
    assert "не перегружать" in guide and "утверждённые владельцем" in guide


def test_finished_copy_preflight_is_required_before_handoff() -> None:
    route = read("README.md")
    guide = read("YOUTUBE_PRESENTATION_BY_SURFACE.md")
    assert "Перед передачей обязательно проверь именно готовый текст" in route
    assert "Приёмка готового YouTube-текста" in guide
    for required in (
        "цитату Писания",
        "окончательным принятым MP4",
        "5000 **UTF-8 байт**",
        "validate_youtube_copy.py",
        "TEXT_PREPARED_NOT_LIVE_VERIFIED",
    ):
        assert required in guide


def test_p0_semantic_audio_evidence_cannot_be_substituted_with_srt() -> None:
    guide = read("YANDEX_TRANSLATION_EDITORIAL.md")
    assert "P0: смысловые подмены" in guide
    for evidence in (
        "EN-оригинал",
        "RU-аудиофрагмент",
        "RU ASR-гипотезу",
        "AUDIO_FIXED",
        "LISTENING_APPROVED",
        "MASTER_ACCEPTED",
        "BLOCKED/NOT_FOR_PUBLICATION",
    ):
        assert evidence in guide
    assert "Правильная строка SRT не исправляет" in guide
