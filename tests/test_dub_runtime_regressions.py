from __future__ import annotations
from tools.voxcpm2.generic_short_production import standardize_russian_title

def test_title_standard_fixes_christian_woman_and_adds_speaker() -> None:
    result = standardize_russian_title('В чем заключается сила христианской женщины', context='Original title: What Is the Strength of a Christian Woman? | John Piper')
    assert result == 'В Чем Заключается Сила Женщины Христианки - Джон Пайпер'

def test_title_standard_preserves_known_mixed_case_words() -> None:
    result = standardize_russian_title('как YouTube и AI меняют мир')
    assert result == 'Как YouTube и AI Меняют Мир'
