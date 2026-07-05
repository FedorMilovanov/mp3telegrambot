"""Tests for v3 minor-debt patch 6:
- core/prompt_rules.py: QA_TIMESTAMP_RULE and STRICT_BANS_STUDY added
- core/prompts.py: _expand_prompt_rules extended with new replacements
- core/prompts.py: STUDY_ANALYSIS uses STRICT_BANS_STUDY via _expand_prompt_rules
- All prompts have no unresolved shared-rule placeholders
"""
import re


# ─── New constants in prompt_rules ────────────────────────────────────────

def test_qa_timestamp_rule_exists_and_has_content():
    from core.prompt_rules import QA_TIMESTAMP_RULE
    assert "ПРАВИЛО ТАЙМКОДОВ ОТНОСИТЕЛЬНО СЕКЦИИ" in QA_TIMESTAMP_RULE
    assert "M:SS" in QA_TIMESTAMP_RULE
    assert "НИКОГДА" in QA_TIMESTAMP_RULE


def test_strict_bans_study_exists_and_has_content():
    from core.prompt_rules import STRICT_BANS_STUDY
    assert "СТРОГИЕ ЗАПРЕТЫ" in STRICT_BANS_STUDY
    assert "главный вывод" in STRICT_BANS_STUDY
    assert "reflection content" in STRICT_BANS_STUDY


# ─── _expand_prompt_rules handles new constants ───────────────────────────

def test_expand_handles_strict_bans_study():
    from core.prompts import _expand_prompt_rules
    result = _expand_prompt_rules("A {STRICT_BANS_STUDY} B")
    assert "{STRICT_BANS_STUDY}" not in result
    assert "главный вывод" in result


def test_expand_handles_qa_timestamp_rule():
    from core.prompts import _expand_prompt_rules
    result = _expand_prompt_rules("X {QA_TIMESTAMP_RULE} Y")
    assert "{QA_TIMESTAMP_RULE}" not in result
    assert "ПРАВИЛО ТАЙМКОДОВ" in result


# ─── STUDY_ANALYSIS_PROMPT ───────────────────────────────────────────────

def test_study_analysis_has_strict_bans():
    from core.prompts import STUDY_ANALYSIS_PROMPT
    assert "СТРОГИЕ ЗАПРЕТЫ" in STUDY_ANALYSIS_PROMPT, \
        "STUDY_ANALYSIS_PROMPT must contain СТРОГИЕ ЗАПРЕТЫ"


def test_study_analysis_has_glavny_vyvod():
    from core.prompts import STUDY_ANALYSIS_PROMPT
    assert "главный вывод" in STUDY_ANALYSIS_PROMPT, \
        "STUDY_ANALYSIS_PROMPT must contain 'главный вывод' from STRICT_BANS_STUDY"


def test_study_analysis_bans_reflection_content():
    from core.prompts import STUDY_ANALYSIS_PROMPT
    assert "reflection content" in STUDY_ANALYSIS_PROMPT, \
        "STUDY_ANALYSIS_PROMPT must ban reflection content"


def test_study_analysis_has_strict_bans_but_not_bloated():
    """STRICT_BANS_STUDY присутствует; AUDIT R5 сократил каталог авторов —
    гардируем содержание, а не «чем длиннее, тем лучше»."""
    from core.prompts import STUDY_ANALYSIS_PROMPT
    assert "REFLECTION, не STUDY" in STUDY_ANALYSIS_PROMPT
    assert 40000 < len(STUDY_ANALYSIS_PROMPT) < 60000, \
        f"got {len(STUDY_ANALYSIS_PROMPT)}"


def test_study_analysis_no_raw_placeholders():
    from core.prompts import STUDY_ANALYSIS_PROMPT
    for ph in ("{STRICT_BANS_STUDY}", "{STRICT_BANS}", "{INLINE_TIMESTAMP_RULES}"):
        assert ph not in STUDY_ANALYSIS_PROMPT, \
            f"STUDY_ANALYSIS still has unresolved placeholder: {ph}"


# ─── All four main prompts: no shared-rule placeholders ──────────────────

def test_all_prompts_no_unresolved_shared_placeholders():
    from core.prompts import (
        SYNOPSIS_PROMPT_V2, SYNOPSIS_PROMPT_QA,
        STUDY_ANALYSIS_PROMPT, REFLECTION_APPLICATION_PROMPT,
    )
    shared_ph = re.compile(
        r"\{(INLINE_TIMESTAMP_RULES|STRICT_BANS|STRICT_BANS_STUDY"
        r"|QA_TIMESTAMP_RULE|AUTHOR_NAMING_RULE|JSON_OUTPUT_RULE"
        r"|SECTION_TITLE_RULE)\}"
    )
    for name, prompt in [
        ("SYNOPSIS_V2",   SYNOPSIS_PROMPT_V2),
        ("SYNOPSIS_QA",   SYNOPSIS_PROMPT_QA),
        ("STUDY_ANALYSIS", STUDY_ANALYSIS_PROMPT),
        ("REFLECTION",    REFLECTION_APPLICATION_PROMPT),
    ]:
        found = shared_ph.findall(prompt)
        assert not found, \
            f"{name} still has unresolved shared placeholders: {found}"
