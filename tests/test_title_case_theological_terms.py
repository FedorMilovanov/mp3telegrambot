"""Regression tests for Russian title casing of theological terms."""

from core.text_utils import title_case_fragment


def test_lords_day_preserved_in_russian_title_case():
    assert title_case_fragment("это день господень?") == "Это день Господень?"
    assert title_case_fragment("день господень и воскресение") == "День Господень и воскресение"
