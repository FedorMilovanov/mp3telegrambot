"""Tests for v3 patch 7 — prompt quality hardening and BUG-R3-01 fix.

Changes:
- core/text_utils.py: git/tech word scrub added to _INLINE_SCRUB_PATTERNS
- core/prompt_rules.py: THIRD_PERSON_BAN + FEW_SHOT_FIRST_SECTION constants
- core/prompts.py: SYNOPSIS_V2 uses new shared rules via _expand_prompt_rules
- core/prompts.py: STUDY_ANALYSIS КРИТИЧЕСКИ ВАЖНО count reduced
"""
import re


# ─── BUG-R3-01: git/tech artifact scrub ──────────────────────────────────────

def test_scrub_removes_git_commit():
    from core.text_utils import _scrub_inline
    result = _scrub_inline("опубли commit вавшего серию")
    assert "commit" not in result, "commit must be scrubbed"


def test_scrub_removes_git_push():
    from core.text_utils import _scrub_inline
    result = _scrub_inline("опубликовал push серию статей")
    assert "push" not in result, "push must be scrubbed"


def test_scrub_removes_git_merge():
    from core.text_utils import _scrub_inline
    assert "merge" not in _scrub_inline("merge конфликт"), "merge must be scrubbed"


def test_scrub_does_not_remove_normal_russian():
    from core.text_utils import _scrub_inline
    text = "Реформация в XVI веке изменила церковь"
    assert _scrub_inline(text) == text, "normal Russian must not be touched"


# ─── New prompt_rules constants ───────────────────────────────────────────────

def test_third_person_ban_exists():
    from core.prompt_rules import THIRD_PERSON_BAN
    assert "ЗАПРЕТ ОТ ТРЕТЬЕГО ЛИЦА" in THIRD_PERSON_BAN
    assert "МакАртур показывает" in THIRD_PERSON_BAN  # negative example
    assert "Монергизм исключает синергизм" in THIRD_PERSON_BAN  # positive example


def test_few_shot_first_section_exists():
    from core.prompt_rules import FEW_SHOT_FIRST_SECTION
    assert "ПРИМЕР ПРАВИЛЬНОГО НАЧАЛА" in FEW_SHOT_FIRST_SECTION
    assert "✅ ПРАВИЛЬНО" in FEW_SHOT_FIRST_SECTION
    assert "❌ ПЛОХО" in FEW_SHOT_FIRST_SECTION


# ─── SYNOPSIS_V2 uses new rules ──────────────────────────────────────────────

def test_synopsis_v2_has_third_person_ban():
    from core.prompts import SYNOPSIS_PROMPT_V2
    assert "ЗАПРЕТ ОТ ТРЕТЬЕГО ЛИЦА" in SYNOPSIS_PROMPT_V2, \
        "SYNOPSIS_V2 must embed THIRD_PERSON_BAN"


def test_synopsis_v2_has_few_shot_example():
    from core.prompts import SYNOPSIS_PROMPT_V2
    assert "ПРИМЕР ПРАВИЛЬНОГО НАЧАЛА" in SYNOPSIS_PROMPT_V2, \
        "SYNOPSIS_V2 must embed FEW_SHOT_FIRST_SECTION"


def test_synopsis_v2_no_raw_placeholders():
    from core.prompts import SYNOPSIS_PROMPT_V2
    shared = re.compile(r"\{[A-Z_]+\}")
    found = shared.findall(SYNOPSIS_PROMPT_V2)
    assert not found, f"SYNOPSIS_V2 has unresolved placeholders: {found}"


def test_synopsis_v2_format_works():
    from core.prompts import SYNOPSIS_PROMPT_V2
    result = SYNOPSIS_PROMPT_V2.format(
        title="Test Sermon", duration="60:00",
        hermeneutic_method="expository",
        format_note="sermon", syn_sections="9",
        syn_section_len="500", syn_total="4500",
    )
    assert "Test Sermon" in result
    assert len(result) > 10000


# ─── STUDY_ANALYSIS КРИТИЧЕСКИ ВАЖНО reduced ─────────────────────────────────

def test_study_analysis_crit_count_reduced():
    from core.prompts import STUDY_ANALYSIS_PROMPT
    count = STUDY_ANALYSIS_PROMPT.count("КРИТИЧЕСКИ ВАЖНО")
    assert count <= 12, \
        f"STUDY КРИТИЧЕСКИ ВАЖНО must be ≤12, got {count} (was 15)"
    assert count >= 4, \
        f"STUDY must keep at least 4 real critical notes, got {count}"


# ─── All prompts no raw shared placeholders ───────────────────────────────────

def test_all_four_prompts_no_placeholders():
    from core.prompts import (
        SYNOPSIS_PROMPT_V2, SYNOPSIS_PROMPT_QA,
        STUDY_ANALYSIS_PROMPT, REFLECTION_APPLICATION_PROMPT,
    )
    shared = re.compile(
        r"\{(INLINE_TIMESTAMP_RULES|STRICT_BANS|STRICT_BANS_STUDY"
        r"|QA_TIMESTAMP_RULE|AUTHOR_NAMING_RULE|JSON_OUTPUT_RULE"
        r"|SECTION_TITLE_RULE|THIRD_PERSON_BAN|FEW_SHOT_FIRST_SECTION)\}"
    )
    for name, p in [
        ("SYNOPSIS_V2",   SYNOPSIS_PROMPT_V2),
        ("SYNOPSIS_QA",   SYNOPSIS_PROMPT_QA),
        ("STUDY_ANALYSIS", STUDY_ANALYSIS_PROMPT),
        ("REFLECTION",    REFLECTION_APPLICATION_PROMPT),
    ]:
        found = shared.findall(p)
        assert not found, f"{name} has unresolved placeholders: {found}"

