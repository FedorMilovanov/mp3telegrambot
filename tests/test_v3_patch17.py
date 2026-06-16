"""Tests for v3 patch 17 — close all open items from patch 15.

Items applied:
1. SYNOPSIS_QA: _expand_prompt_rules + VERIFIED_CONTEXT_RULE + 3rd-person check
2. REFLECTION: VERIFIED_CONTEXT_RULE
3. SYNOPSIS_V2: 3rd-person check in final checklist
4. test_v3_patch14.py: medium → high (quality-first)
Context Caching: skipped (incompatible with thinking_level=high)
"""
import re


def test_qa_has_expand():
    src = open("core/prompts.py", encoding="utf-8").read()
    assert "SYNOPSIS_PROMPT_QA = _expand_prompt_rules(SYNOPSIS_PROMPT_QA)" in src


def test_qa_has_verified_context():
    from core.prompts import SYNOPSIS_PROMPT_QA
    assert "ВЕРИФИЦИРОВАННОГО КОНТЕКСТА" in SYNOPSIS_PROMPT_QA


def test_qa_has_third_person_check():
    from core.prompts import SYNOPSIS_PROMPT_QA
    # QA final checklist must warn about 3rd person
    assert "фамилия/роль автора" in SYNOPSIS_PROMPT_QA or "автор/проповедник/спикер" in SYNOPSIS_PROMPT_QA


def test_qa_no_raw_shared_placeholders():
    from core.prompts import SYNOPSIS_PROMPT_QA
    shared = re.compile(
        r"\{(INLINE_TIMESTAMP_RULES|STRICT_BANS|QA_TIMESTAMP_RULE"
        r"|THIRD_PERSON_BAN|VERIFIED_CONTEXT_RULE|FEW_SHOT_FIRST_SECTION)\}"
    )
    assert not shared.findall(SYNOPSIS_PROMPT_QA)


def test_qa_format_works():
    from core.prompts import SYNOPSIS_PROMPT_QA
    r = SYNOPSIS_PROMPT_QA.format(
        title="Q&A Test", duration="60:00", timestamps_block="0:00 Вопрос 1"
    )
    assert "Q&A Test" in r


def test_reflection_has_verified_context():
    from core.prompts import REFLECTION_APPLICATION_PROMPT
    assert "ВЕРИФИЦИРОВАННОГО КОНТЕКСТА" in REFLECTION_APPLICATION_PROMPT


def test_reflection_no_raw_shared_placeholders():
    from core.prompts import REFLECTION_APPLICATION_PROMPT
    shared = re.compile(
        r"\{(INLINE_TIMESTAMP_RULES|STRICT_BANS|QA_TIMESTAMP_RULE"
        r"|THIRD_PERSON_BAN|VERIFIED_CONTEXT_RULE|FEW_SHOT_FIRST_SECTION)\}"
    )
    assert not shared.findall(REFLECTION_APPLICATION_PROMPT)


def test_synopsis_v2_final_checklist_has_third_person():
    from core.prompts import SYNOPSIS_PROMPT_V2
    # After final checklist header, must have third-person check
    fin_pos = SYNOPSIS_PROMPT_V2.rfind("ФИНАЛЬНАЯ ПРОВЕРКА")
    fin_block = SYNOPSIS_PROMPT_V2[fin_pos:] if fin_pos != -1 else ""
    assert "имя/роль автора" in fin_block or "фамилия автора" in fin_block, \
        "V2 final checklist must warn about 3rd person writing"


def test_all_four_prompts_fully_clean():
    from core.prompts import (SYNOPSIS_PROMPT_V2, SYNOPSIS_PROMPT_QA,
        STUDY_ANALYSIS_PROMPT, REFLECTION_APPLICATION_PROMPT)
    shared = re.compile(
        r"\{(INLINE_TIMESTAMP_RULES|STRICT_BANS|STRICT_BANS_STUDY|QA_TIMESTAMP_RULE"
        r"|THIRD_PERSON_BAN|FEW_SHOT_FIRST_SECTION|VERIFIED_CONTEXT_RULE|AUTHOR_NAMING_RULE"
        r"|JSON_OUTPUT_RULE|SECTION_TITLE_RULE)\}"
    )
    for name, p in [("V2", SYNOPSIS_PROMPT_V2), ("QA", SYNOPSIS_PROMPT_QA),
                    ("STUDY", STUDY_ANALYSIS_PROMPT), ("REFL", REFLECTION_APPLICATION_PROMPT)]:
        found = shared.findall(p)
        assert not found, f"{name} has unresolved: {found}"


def test_patch14_test_updated_for_quality_first():
    src = open("tests/test_v3_patch14.py", encoding="utf-8").read()
    assert "test_reflection_reverted_to_high_quality" in src, \
        "patch14 test must be updated to reflect quality-first decision"
    assert "test_reflection_uses_medium_thinking" not in src, \
        "old medium test must be removed"

