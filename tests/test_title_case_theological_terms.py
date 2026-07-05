"""Regression tests for Russian title casing of theological terms."""

from core.text_utils import normalize_common_typos, title_case_fragment


def test_lords_day_preserved_in_russian_title_case():
    assert title_case_fragment("это день господень?") == "Это День Господень?"
    assert title_case_fragment("день господень и воскресение") == "День Господень и Воскресение"


def test_biblical_author_names_are_capitalized_in_text():
    text = "как я уже говорил, матфею важно показать мысль. матфей не беспокоится о хронологии."
    fixed = normalize_common_typos(text)
    assert "Матфею" in fixed
    assert "Матфей не беспокоится" in fixed
