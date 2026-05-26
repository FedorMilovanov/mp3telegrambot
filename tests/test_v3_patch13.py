"""Tests for v3 patch 13 — VERIFIED_CONTEXT_RULE: safe historical enrichment in Synopsis.

Research basis:
- Gemini 3.5 Flash document-grounded hallucination: ~5-10% (acceptable)
- Gemini 3.5 Flash open-question hallucination: 61% (too high without guardrails)
- Solution: allow verified historical/theological context with strict guardrails

Changes:
- core/prompt_rules.py: VERIFIED_CONTEXT_RULE with allowed/forbidden categories + test
- core/prompts.py: SYNOPSIS_V2 uses VERIFIED_CONTEXT_RULE via _expand_prompt_rules
"""
import re


def test_verified_context_rule_exists():
    from core.prompt_rules import VERIFIED_CONTEXT_RULE
    assert "ПРАВИЛО ВЕРИФИЦИРОВАННОГО КОНТЕКСТА" in VERIFIED_CONTEXT_RULE


def test_verified_context_rule_has_allowed_categories():
    from core.prompt_rules import VERIFIED_CONTEXT_RULE
    assert "РАЗРЕШЁННЫЕ КАТЕГОРИИ" in VERIFIED_CONTEXT_RULE, \
        "Must specify what IS allowed"
    # Must allow historical biblical figures
    assert "персонажей из Библии" in VERIFIED_CONTEXT_RULE


def test_verified_context_rule_has_forbidden_categories():
    from core.prompt_rules import VERIFIED_CONTEXT_RULE
    assert "ЗАПРЕЩЁННЫЕ КАТЕГОРИИ" in VERIFIED_CONTEXT_RULE, \
        "Must specify what is NOT allowed"
    # Must forbid direct quotes from books
    assert "Прямые цитаты" in VERIFIED_CONTEXT_RULE


def test_verified_context_rule_has_self_test():
    from core.prompt_rules import VERIFIED_CONTEXT_RULE
    assert "ТЕСТ ПЕРЕД ДОБАВЛЕНИЕМ" in VERIFIED_CONTEXT_RULE, \
        "Must provide self-verification test for model"


def test_synopsis_v2_has_verified_context_rule():
    from core.prompts import SYNOPSIS_PROMPT_V2
    assert "ПРАВИЛО ВЕРИФИЦИРОВАННОГО КОНТЕКСТА" in SYNOPSIS_PROMPT_V2, \
        "SYNOPSIS_V2 must embed VERIFIED_CONTEXT_RULE"


def test_synopsis_v2_no_raw_placeholders():
    from core.prompts import SYNOPSIS_PROMPT_V2
    shared = re.compile(
        r"\{(INLINE_TIMESTAMP_RULES|STRICT_BANS|QA_TIMESTAMP_RULE"
        r"|THIRD_PERSON_BAN|FEW_SHOT_FIRST_SECTION|VERIFIED_CONTEXT_RULE)\}"
    )
    found = shared.findall(SYNOPSIS_PROMPT_V2)
    assert not found, f"SYNOPSIS_V2 has unresolved: {found}"


def test_synopsis_v2_format_works():
    from core.prompts import SYNOPSIS_PROMPT_V2
    r = SYNOPSIS_PROMPT_V2.format(
        title="Test", duration="50:00",
        hermeneutic_method="expository",
        format_note="sermon", syn_sections="7",
        syn_section_len="500", syn_total="3500",
    )
    assert "Test" in r
    assert "ТЕСТ ПЕРЕД ДОБАВЛЕНИЕМ" in r, \
        "Formatted synopsis must contain the verification test"


def test_expand_handles_verified_context():
    from core.prompts import _expand_prompt_rules
    result = _expand_prompt_rules("Начало {VERIFIED_CONTEXT_RULE} конец")
    assert "{VERIFIED_CONTEXT_RULE}" not in result
    assert "ПРАВИЛО ВЕРИФИЦИРОВАННОГО КОНТЕКСТА" in result

