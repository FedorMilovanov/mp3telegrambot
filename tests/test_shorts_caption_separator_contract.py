import inspect

import core.text_utils as text_utils
import services.shorts_video as shorts_video
from services.shorts_video import _prepare_short_hook, build_short_caption


def _caption(hook: str, *, kind: str = "") -> str:
    return build_short_caption(
        candidate={"hook": hook, "kind": kind, "hashtags": []},
        performer="",
        real_author="Пол Вошер",
        real_event="",
        format_name="sermon",
    )


def _title_runtime_diagnostic() -> str:
    fn = shorts_video.title_case_fragment
    try:
        source = inspect.getsource(fn)
    except Exception as exc:
        source = f"<source unavailable: {type(exc).__name__}: {exc}>"
    return (
        f"shorts_fn={fn!r}; module={getattr(fn, '__module__', '')}; "
        f"qualname={getattr(fn, '__qualname__', '')}; "
        f"same_as_core={fn is text_utils.title_case_fragment}; source={source}"
    )


def test_internal_title_pipeline_preserves_em_dash_before_outer_formatting() -> None:
    prepared = _prepare_short_hook("Сомнение - Это не слабость", "Пол Вошер")
    assert prepared == "Сомнение — Это не слабость"
    titled = shorts_video.title_case_fragment(prepared)
    assert titled == "Сомнение — Это не Слабость", _title_runtime_diagnostic()
    assert shorts_video.html_mod.escape(titled) == "Сомнение — Это не Слабость"


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


def test_exclamation_keeps_punctuation_and_still_has_author_separator() -> None:
    assert _caption("Бодрствуйте и стойте в вере!") == (
        "Бодрствуйте и Стойте в Вере! - Пол Вошер"
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
