"""Regression contract for direct agent video naming and YouTube-specific metadata.
No provider calls, no media mutations, no bot mode changes.
"""
from __future__ import annotations

import re
from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "docs" / "agent_direct_dubbing"


def text(name: str) -> str:
    return (DOC / name).read_text(encoding="utf-8")


def test_agent_route_reads_surface_style_contract() -> None:
    readme = text("README.md")
    assert "[`YOUTUBE_PRESENTATION_BY_SURFACE.md`](YOUTUBE_PRESENTATION_BY_SURFACE.md)" in readme
    assert "[`RUSSIAN_NAMING_AND_DELIVERY.md`](RUSSIAN_NAMING_AND_DELIVERY.md)" in readme
    assert readme.index("YOUTUBE_PRESENTATION_BY_SURFACE.md") < readme.index(
        "RUSSIAN_NAMING_AND_DELIVERY.md"
    )
    assert "YOUTUBE_PRESENTATION_BY_SURFACE.md" in text("CLIPS_SHORTS_AND_HANDOFF.md")
    assert "YOUTUBE_PRESENTATION_BY_SURFACE.md" in text("RUSSIAN_NAMING_AND_DELIVERY.md")


def test_russian_filename_contract_prevents_repeated_operator_regressions() -> None:
    naming = text("RUSSIAN_NAMING_AND_DELIVERY.md")
    assert "ASCII U+002D" in naming
    assert "` - `" in naming
    assert "Title Case" in naming
    assert "только запрошенные готовые видео" in naming
    assert "TXT" in naming and "не создавать" in naming.lower()
    example = (
        "Джон Пайпер - Предопределяет ли Бог Людей к Аду "
        "- Спроси Пастора Джона №450.mp4"
    )
    assert example in naming
    examples = re.findall(r"`([^`\n]*\.mp4)`", naming)
    assert len(examples) >= 3
    assert all("—" not in name and "–" not in name for name in examples)
    assert all("Русская Озвучка" not in name for name in examples)
    assert all("Русский Перевод" not in name for name in examples)
    assert "01 - Автор - Тема Выпуска.mp4" in examples


def test_youtube_emphasis_differs_from_chatgpt_markdown() -> None:
    guide = text("YOUTUBE_PRESENTATION_BY_SURFACE.md")
    assert "`*важные слова*`" in guide
    assert "`_выделенные слова_`" in guide
    assert "`-ненужный текст-`" in guide
    assert "`**важные слова**`" in guide
    assert "`*VK:*`" in guide
    assert "пунктуация зависит от смысла" in guide
    assert "первый" in guide.lower()
    assert "на одной строке" in guide
    assert "не автоматическая ошибка" in guide


def test_independent_channel_contracts_and_delivery_boundary() -> None:
    guide = text("YOUTUBE_PRESENTATION_BY_SURFACE.md")
    for name in ("The Legendary Poet", "Господь Бог", "⚡", "Title Case"):
        assert name in guide
    assert "пять" in guide.lower() or "5" in guide
    assert "5000 БАЙТ" in guide
    assert "не кликабельны" in guide
    assert "00:00" in guide
    assert "Correction:" in guide
    assert "Без TXT к MP4" in guide
    assert "видео" in guide.lower()


def test_no_sidecars_even_for_batch_and_no_auto_provider_write() -> None:
    naming = text("RUSSIAN_NAMING_AND_DELIVERY.md")
    guide = text("YOUTUBE_PRESENTATION_BY_SURFACE.md")
    assert "десяти" in naming or "10" in naming
    assert "без" in naming.lower() and "описаний" in naming.lower()
    assert "не разрешаются никакие публикационные записи" in guide
    assert "Не публиковать" in guide or "не разрешение публиковать" in guide


def test_video_clock_words_are_repaired_before_resynthesis() -> None:
    runbook = text("YANDEX_TRANSLATION_EDITORIAL.md")
    assert "сначала попробовать вырезать только ошибочные слова" in runbook
    assert "часов" in runbook and "минут" in runbook
    assert "10–25 мс" in runbook
    assert "за пределами окна идентичны" in runbook
    assert "только короткую корректную фразу" in runbook
    assert "не исправляет неправильный перевод самой цитаты" in runbook


def test_qa_and_rights_are_independent() -> None:
    readme = text("README.md")
    handoff = text("CLIPS_SHORTS_AND_HANDOFF.md")
    naming = text("RUSSIAN_NAMING_AND_DELIVERY.md")
    assert "LOCAL_HANDOFF_READY" in readme
    assert "RIGHTS_APPROVED" in readme
    assert "LOCAL_HANDOFF_READY" in handoff and "RIGHTS_APPROVED" in handoff
    assert "Неподтверждённые права на публикацию сами по себе не препятствуют локальной передаче" in naming
    assert "не даёт права публиковать" in handoff


def test_other_repository_evidence_is_not_converted_or_copied_blindly() -> None:
    guide = text("YOUTUBE_PRESENTATION_BY_SURFACE.md")
    assert "Research/AGENT_RULES.md" in guide
    assert "gb-is-my-strength/docs/EDITORIAL-SOURCE-POLICY.md" in guide
    assert "TheLegendaryPoet/docs/EDITORIAL_JUDGMENT_AND_SOURCE_POLICY.md" in guide
    assert "video-channel-manager/docs/youtube-copy-authoring-standard.md" in guide
    assert "A1/A2/A3/B1/C/D" in guide
    assert "A+/A/B/C" in guide
    assert "не перекодировать молча" in guide
    assert "английский проповедник" in guide.lower()


def test_quick_operator_routing_is_explicit_for_one_and_ten_mp4() -> None:
    readme = text("README.md")
    assert "Переведи и положи 1 видео" in readme
    assert "Переведи и положи 10 видео" in readme
    assert "никаких лишних" in readme.lower() or "Не генерировать TXT" in readme
    assert "Описание" in readme or "описание" in readme
    assert "`01 - ...`" in readme
    clips = text("CLIPS_SHORTS_AND_HANDOFF.md")
    assert "`01 — …`" not in clips
    assert "`01 - …`" in clips
    assert "SRT сохранять **во внутренней рабочей папке**" in clips


def test_em_dash_not_used_as_file_title_separator() -> None:
    for name in ("README.md", "CLIPS_SHORTS_AND_HANDOFF.md", "RUSSIAN_NAMING_AND_DELIVERY.md"):
        t = text(name)
        examples = re.findall(r"`([^`]+\.mp4)`", t)
        assert examples, name
        assert all("—" not in example and "–" not in example for example in examples), name
        assert all("Русская Озвучка" not in example for example in examples), name
