from core.analysis_profiles import get_expanded_analysis_profile


def test_long_study_profile_does_not_force_translation_or_original_language():
    profile = get_expanded_analysis_profile(90 * 60, "study")
    assert profile.original_languages.startswith("0–")
    assert profile.translation_forks.startswith("0–")
    block = profile.prompt_block("study")
    assert "Нулевой результат" in block
    assert "не меняют понимание текста" in block


def test_very_long_material_only_raises_ceiling():
    profile = get_expanded_analysis_profile(150 * 60, "study")
    assert profile.original_languages.startswith("0–4")
    assert profile.translation_forks.startswith("0–3")
    assert "длина материала не делает их обязательными" in profile.translation_forks


def test_reflection_profile_starts_with_truth_and_assimilation():
    profile = get_expanded_analysis_profile(75 * 60, "reflection")
    block = profile.prompt_block("reflection")
    assert "Сначала истина Писания" in block
    assert "её понимание и усвоение" in block
    assert "применение — только как реальный плод" in block


def test_short_study_profile_allows_zero_research_layers():
    profile = get_expanded_analysis_profile(12 * 60, "study")
    assert profile.source_focus.startswith("0–3")
    assert profile.original_languages.startswith("0–1")
    assert profile.translation_forks.startswith("0–1")
