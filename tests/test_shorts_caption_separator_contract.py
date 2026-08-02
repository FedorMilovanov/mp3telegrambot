from services.shorts_video import (
    _prepare_short_hook,
    build_short_caption,
)


def _caption(hook: str, *, kind: str = "") -> str:
    return build_short_caption(
        candidate={"hook": hook, "kind": kind, "hashtags": []},
        performer="",
        real_author="Пол Вошер",
        real_event="",
        format_name="sermon",
    )


def test_internal_pause_uses_em_dash_and_author_boundary_uses_hyphen() -> None:
    assert _caption(
        "Сомнение - Это не Просто Слабость, Это Прямое Оскорбление Характера Бога"
    ) == (
        "Сомнение — Это не Просто Слабость, Это Прямое Оскорбление "
        "Характера Бога - Пол Вошер"
    )


def test_question_keeps_punctuation_and_still_has_author_separator() -> None:
    assert _caption("Кто такой настоящий мужчина в браке?") == (
        "Кто Такой Настоящий Мужчина в Браке? - Пол Вошер"
    )


def test_hyphens_inside_words_are_not_changed() -> None:
    prepared = _prepare_short_hook("Действовать по-мужски - Прямой призыв", "Пол Вошер")
    assert prepared == "Действовать по-мужски — Прямой призыв"


def test_exact_trailing_author_suffix_is_removed_before_outer_formatting() -> None:
    assert _caption("Сомнение — Это Оскорбление Бога - Пол Вошер") == (
        "Сомнение — Это Оскорбление Бога - Пол Вошер"
    )


def test_quote_format_preserves_separator_contract() -> None:
    assert _caption("Сомнение - Это оскорбление Бога", kind="quote") == (
        "«Сомнение — Это Оскорбление Бога» - Пол Вошер"
    )
