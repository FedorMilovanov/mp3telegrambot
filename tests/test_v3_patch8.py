"""Tests for v3 patch 8 — THIRD_PERSON_BAN in Reflection and audio prompt."""
import re

def test_reflection_has_third_person_ban():
    from core.prompts import REFLECTION_APPLICATION_PROMPT
    assert "ЗАПРЕТ ОТ ТРЕТЬЕГО ЛИЦА" in REFLECTION_APPLICATION_PROMPT

def test_reflection_no_raw_placeholders():
    from core.prompts import REFLECTION_APPLICATION_PROMPT
    assert not re.findall(r"\{[A-Z_]+\}", REFLECTION_APPLICATION_PROMPT)

def test_reflection_longer_than_baseline():
    from core.prompts import REFLECTION_APPLICATION_PROMPT
    assert len(REFLECTION_APPLICATION_PROMPT) > 28846

def test_audio_prompt_has_style_note():
    from core.prompts import build_audio_analysis_prompt
    p = build_audio_analysis_prompt("Test", "Chan", "50:00", 3000)
    assert "Пиши безлично или от первого лица автора" in p

def test_audio_prompt_has_bad_example():
    from core.prompts import build_audio_analysis_prompt
    p = build_audio_analysis_prompt("Test", "Chan", "50:00", 3000)
    assert "МакАртур показывает" in p

def test_all_prompts_no_shared_placeholders():
    from core.prompts import (SYNOPSIS_PROMPT_V2, SYNOPSIS_PROMPT_QA,
        STUDY_ANALYSIS_PROMPT, REFLECTION_APPLICATION_PROMPT)
    shared = re.compile(r"\{(INLINE_TIMESTAMP_RULES|STRICT_BANS|STRICT_BANS_STUDY"
        r"|QA_TIMESTAMP_RULE|AUTHOR_NAMING_RULE|JSON_OUTPUT_RULE"
        r"|SECTION_TITLE_RULE|THIRD_PERSON_BAN|FEW_SHOT_FIRST_SECTION)\}")
    for name, p in [("V2", SYNOPSIS_PROMPT_V2), ("QA", SYNOPSIS_PROMPT_QA),
                    ("STUDY", STUDY_ANALYSIS_PROMPT), ("REFL", REFLECTION_APPLICATION_PROMPT)]:
        found = shared.findall(p)
        assert not found, f"{name} has unresolved: {found}"

def test_synopsis_v2_still_intact():
    from core.prompts import SYNOPSIS_PROMPT_V2
    assert "ЗАПРЕТ ОТ ТРЕТЬЕГО ЛИЦА" in SYNOPSIS_PROMPT_V2
    assert "ПРИМЕР ПРАВИЛЬНОГО НАЧАЛА" in SYNOPSIS_PROMPT_V2
    assert not re.findall(r"\{[A-Z_]+\}", SYNOPSIS_PROMPT_V2)
