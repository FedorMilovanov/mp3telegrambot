"""Regression tests for v3 patch 27 — duration-aware expanded analysis profiles."""

from pathlib import Path

from core.analysis_profiles import get_expanded_analysis_profile
from core.prompts import REFLECTION_APPLICATION_PROMPT, STUDY_ANALYSIS_PROMPT


def test_expanded_analysis_profiles_scale_by_duration_and_kind():
    short_study = get_expanded_analysis_profile(10 * 60, "study")
    medium_study = get_expanded_analysis_profile(40 * 60, "study")
    long_study = get_expanded_analysis_profile(75 * 60, "study")

    assert short_study.name == "fast"
    assert medium_study.name == "balanced"
    assert long_study.name == "deep"
    assert short_study.max_tokens < medium_study.max_tokens < long_study.max_tokens
    assert "0–1 ключевое слово" in short_study.prompt_block("study")
    assert "2–3 ключевых слова" in long_study.prompt_block("study")

    short_reflection = get_expanded_analysis_profile(10 * 60, "reflection")
    long_reflection = get_expanded_analysis_profile(75 * 60, "reflection")
    assert short_reflection.max_tokens < long_reflection.max_tokens
    assert "Пасторская логика" in long_reflection.prompt_block("reflection")


def test_study_and_reflection_prompts_accept_profile_through_synopsis_context():
    profile = get_expanded_analysis_profile(3700, "study").prompt_block("study")
    study = STUDY_ANALYSIS_PROMPT.format(
        title="T", author="A", duration="1:01:40", format_name="sermon",
        hermeneutic_method="expository", main_topic="m", analysis_summary="a",
        argument_arc="arc", key_categories="k", timestamps="0:00 t",
        concepts="c", scripture="s", translations="tr", lexicon_notes="l",
        synopsis_context=profile, source_pack="sources",
    )
    assert "ПРОФИЛЬ ГЛУБИНЫ ДЛЯ ЭТОГО МАТЕРИАЛА" in study
    assert "Профиль: deep" in study

    refl_profile = get_expanded_analysis_profile(600, "reflection").prompt_block("reflection")
    reflection = REFLECTION_APPLICATION_PROMPT.format(
        title="T", author="A", duration="10:00", hermeneutic_method="mixed",
        main_topic="m", analysis_summary="a", argument_arc="arc",
        key_categories="k", questions_block="- q", timestamps_block="- 0:00 t",
        synopsis_context=refl_profile,
    )
    assert "ПРОФИЛЬ ГЛУБИНЫ ДЛЯ ЭТОГО МАТЕРИАЛА" in reflection
    assert "Профиль: fast" in reflection


def test_telegraph_pages_wires_profiles_to_max_tokens_and_context():
    src = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert "from core.analysis_profiles import get_expanded_analysis_profile" in src
    assert "_study_profile = get_expanded_analysis_profile" in src
    assert "_reflection_profile = get_expanded_analysis_profile" in src
    assert "synopsis_context=_synopsis_context_for_prompt" in src
    assert "max_tokens=_study_profile.max_tokens" in src
    assert "max_tokens=_reflection_profile.max_tokens" in src
