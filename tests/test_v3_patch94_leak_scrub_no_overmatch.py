#!/usr/bin/env python3
"""Regression: _scrub_prompt_context_leaks must not damage legitimate text.

Bug found by audit (2026-05): _CHANNEL_POSITION_RE used `[^.?!…]*` as a
sentence boundary, which broke on the period inside Bible abbreviations
("Рим.", "Ис.", "Ин."). Combined with the over-broad "мы придерживаемся"
trigger, it corrupted author speech, e.g.:

    "Как в Рим. 8:28 показано, мы придерживаемся монергизма. Это ключевой текст."
    -> "Как в Рим.Это ключевой текст."   (WRONG)

It also left glued sentences without a space after removing a leak:
    "...приговор. Наш канал ... Покаяние..." -> "...приговор.Покаяние..."

This test pins the corrected behavior.
"""
from core.content_audit import _scrub_prompt_context_leaks


def test_leak_scrub_does_not_break_author_speech_with_scripture_abbrev():
    text = "Как в Рим. 8:28 показано, мы придерживаемся монергизма. Это ключевой текст."
    out, changed = _scrub_prompt_context_leaks(text)
    # Legitimate preacher speech ("мы придерживаемся") next to a Bible
    # abbreviation must be left completely untouched.
    assert out == text
    assert changed is False
    assert "Как в Рим." not in out.replace("Как в Рим. 8:28", "")  # not truncated to "Рим."


def test_leak_scrub_removes_channel_leak_but_keeps_neighbors_with_space():
    text = (
        "Оправдание — это Божий приговор. "
        "Наш канал придерживается реформатского взгляда. "
        "Покаяние — поворот воли."
    )
    out, changed = _scrub_prompt_context_leaks(text)
    assert changed is True
    assert "Наш канал" not in out
    # Neighboring sentences must survive and not be glued together.
    assert "Оправдание — это Божий приговор." in out
    assert "Покаяние — поворот воли." in out
    assert "приговор.Покаяние" not in out  # no glue
    assert "приговор. Покаяние" in out      # space preserved


def test_leak_scrub_restores_space_after_scripture_ref():
    text = (
        "Сперджен пишет «трава засыхает» (Ис. 40:6). "
        "Наш канал занимает позицию цессационизма. "
        "Дальше идёт разбор."
    )
    out, changed = _scrub_prompt_context_leaks(text)
    assert changed is True
    assert "Наш канал" not in out
    assert "(Ис. 40:6).Дальше" not in out   # no glue
    assert "Дальше идёт разбор." in out


def test_leak_scrub_leaves_clean_text_untouched():
    text = "Иаков 4:14 говорит о краткости жизни. Этот образ повторяется у пророков."
    out, changed = _scrub_prompt_context_leaks(text)
    assert out == text
    assert changed is False


def test_leak_scrub_still_removes_editorial_position_variants():
    for leak in (
        "Редакторская позиция канала — цессационизм. А вот сам автор говорит иначе.",
        "Наш канал придерживается LBCF 1689. Далее разбор аргумента.",
        "Конфессиональная рамка канала здесь определяет акценты. Текст продолжается.",
    ):
        out, changed = _scrub_prompt_context_leaks(leak)
        assert changed is True, leak
        assert "канал" not in out.lower(), out
