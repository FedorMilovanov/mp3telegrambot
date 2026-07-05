"""Tests for v3 patch 14 — Reflection thinking_level=medium (cost optimization).

Research basis: 5-run analysis shows Reflection thoughts avg 7556 tokens.
Pastoral/applicative style doesn't need deep reasoning (high).
Study Analysis keeps high — theological depth requires it.

Changes:
- services/telegraph_pages.py: _run_expanded_pipeline accepts thinking_level param
- services/telegraph_pages.py: Reflection uses thinking_level="medium"
- Study Analysis stays with default thinking_level="high"
"""


def test_run_expanded_pipeline_has_thinking_level_param():
    src = open("services/telegraph_pages.py", encoding="utf-8").read()
    assert 'thinking_level: str = "high"' in src, \
        "_run_expanded_pipeline must have thinking_level parameter with default high"


def test_run_expanded_pipeline_passes_thinking_level():
    src = open("services/telegraph_pages.py", encoding="utf-8").read()
    # Both Gemini calls inside must use the parameter, not hardcoded "high"
    assert 'thinking_level=thinking_level,' in src, \
        "_run_expanded_pipeline must pass thinking_level to _gemini_text_request"


def test_reflection_reverted_to_high_quality():
    # V3-P15+17: quality first — Reflection uses default high.
    # AUDIT R5: окно расширено до конца вызова — override medium однажды
    # вернулся НИЖЕ старого окна +500 симв. и тихо жил, обходя этот тест.
    src = open("services/telegraph_pages.py", encoding="utf-8").read()
    refl_pos = src.find('label="ReflectionApplication"')
    assert refl_pos != -1
    refl_block = src[max(0, refl_pos - 20):refl_pos + 1500]
    assert 'thinking_level="medium"' not in refl_block, \
        "Reflection must use default high, not medium"
    assert 'thinking_level=' not in refl_block or 'thinking_level="high"' in refl_block


def test_study_keeps_default_high():
    src = open("services/telegraph_pages.py", encoding="utf-8").read()
    # Study must NOT override (uses default high from _run_expanded_pipeline)
    # Find the StudyAnalysis _run_expanded_pipeline call
    study_pos = src.find('label="StudyAnalysis"')
    assert study_pos != -1
    study_block = src[study_pos:study_pos+500]
    # Must NOT have thinking_level in the call (uses default high)
    import re
    has_explicit_tl = re.search(r'thinking_level\s*=\s*["\']', study_block)
    assert not has_explicit_tl, \
        "StudyAnalysis must use default thinking_level (high) — don't override"


def test_reflection_cost_optimization_documented():
    """Both calls inside _run_expanded_pipeline must use thinking_level param."""
    src = open("services/telegraph_pages.py", encoding="utf-8").read()
    # Count how many times hardcoded "high" appears in _gemini_text_request calls
    import re
    # These should now use thinking_level=thinking_level, not hardcoded
    hardcoded = re.findall(
        r'_gemini_text_request\s*\([^)]*thinking_level\s*=\s*"high"[^)]*\)',
        src, re.DOTALL
    )
    assert not hardcoded, \
        f"Found hardcoded thinking_level='high' in _gemini_text_request: {hardcoded}"

