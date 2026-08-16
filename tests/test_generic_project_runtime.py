from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.voxcpm2.generic_project_runtime import (
    choose_caption_track,
    parse_custom_translation,
    parse_manual_vtt,
    safe_russian_filename,
    validate_custom_timing,
    write_translation_template,
)
from tools.voxcpm2.generic_short_production import standardize_russian_title


def _groups() -> list[dict]:
    return [
        {"id": 1, "start": 0.0, "end": 5.0, "source": "First source sentence."},
        {"id": 2, "start": 5.0, "end": 11.0, "source": "Second source sentence."},
    ]


def test_manual_caption_is_preferred_over_automatic() -> None:
    kind, language = choose_caption_track(
        {
            "language": "en",
            "subtitles": {"en": [{"ext": "vtt"}]},
            "automatic_captions": {"en": [{"ext": "vtt"}]},
        }
    )
    assert (kind, language) == ("manual", "en")


def test_automatic_caption_is_used_when_manual_missing() -> None:
    assert choose_caption_track({"automatic_captions": {"en-US": [{}]}}) == ("automatic", "en-US")


def test_manual_vtt_keeps_both_human_caption_lines(tmp_path: Path) -> None:
    path = tmp_path / "manual.vtt"
    path.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:03.000\nFirst human line\nSecond human line\n",
        encoding="utf-8",
    )
    cues = parse_manual_vtt(path)
    assert [cue.text for cue in cues] == ["First human line Second human line"]


def test_title_filename_keeps_cyrillic_and_removes_windows_chars() -> None:
    assert safe_russian_filename('Почему: Христос / наша "надежда"?') == "Почему Христос наша надежда"


def test_dub_title_reuses_shorts_case_and_keeps_service_words_lowercase() -> None:
    assert standardize_russian_title(
        "сила и достоинство благочестивой женщины",
        context="John Piper",
    ) == "Сила и Достоинство Благочестивой Женщины - Джон Пайпер"


def test_dub_title_keeps_initial_preposition_capitalized() -> None:
    assert standardize_russian_title(
        "в чем заключается сила христианской женщины",
        context="Джон Пайпер",
    ) == "В Чем Заключается Сила Женщины Христианки - Джон Пайпер"


def test_template_round_trip_preserves_user_words(tmp_path: Path) -> None:
    template = tmp_path / "translation.txt"
    write_translation_template(_groups(), template, title="Тест", caption_origin="manual", language="en")
    text = template.read_text(encoding="utf-8")
    text = text.replace("RU:\n\n[2]", "RU: Первый русский блок.\n\n[2]")
    text = text.replace("RU:\n", "RU: Второй русский блок.\n", 1)
    result = parse_custom_translation(text, _groups())
    assert result == [
        {"id": 1, "russian": "Первый русский блок."},
        {"id": 2, "russian": "Второй русский блок."},
    ]


def test_custom_json_requires_all_ids() -> None:
    with pytest.raises(RuntimeError, match="Нарушены ID"):
        parse_custom_translation(json.dumps({"segments": [{"id": 1, "russian": "Один"}]}), _groups())


def test_custom_timing_reports_only_overloaded_lines() -> None:
    translations = [
        {"id": 1, "russian": "Короткая нормальная реплика"},
        {"id": 2, "russian": " ".join(["слово"] * 30)},
    ]
    warnings = validate_custom_timing(translations, _groups())
    assert len(warnings) == 1
    assert "[2]" in warnings[0]
