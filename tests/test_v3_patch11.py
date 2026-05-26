"""Tests for v3 patch 11 — SOURCE_PACKS integrated into Study Analysis prompt.

Changes:
- core/prompts.py: STUDY_ANALYSIS_PROMPT has {source_pack} placeholder in УРОВЕНЬ 2
- services/telegraph_pages.py: imports get_source_pack_for_ai_data and passes source_pack to format()
"""
import re


# ─── prompts.py ───────────────────────────────────────────────────────────────

def test_study_prompt_has_source_pack_placeholder():
    from core.prompts import STUDY_ANALYSIS_PROMPT
    assert "{source_pack}" in STUDY_ANALYSIS_PROMPT, \
        "STUDY_ANALYSIS_PROMPT must have {source_pack} placeholder in УРОВЕНЬ 2"


def test_study_prompt_source_pack_in_level2():
    from core.prompts import STUDY_ANALYSIS_PROMPT
    idx = STUDY_ANALYSIS_PROMPT.find("{source_pack}")
    assert idx != -1
    # Must be near УРОВЕНЬ 2
    context = STUDY_ANALYSIS_PROMPT[max(0, idx-500):idx+100]
    assert "УРОВЕНЬ 2" in context, \
        "{source_pack} must be inside УРОВЕНЬ 2 block"


def test_study_prompt_formats_with_source_pack():
    from core.prompts import STUDY_ANALYSIS_PROMPT
    from core.source_packs import get_source_pack
    pack = get_source_pack(["монергизм"], "")
    result = STUDY_ANALYSIS_PROMPT.format(
        title="T", author="A", duration="50:00",
        format_name="sermon", hermeneutic_method="expository",
        main_topic="t", analysis_summary="a", argument_arc="b",
        key_categories="k", timestamps="0:00 t",
        concepts="", scripture="", translations="", lexicon_notes="",
        synopsis_context="", source_pack=pack,
    )
    assert "1689 LBCF" in result or "Canons of Dort" in result, \
        "source_pack content must appear in formatted Study prompt"


def test_study_prompt_no_raw_source_pack_placeholder():
    """After format(), {source_pack} must be gone."""
    from core.prompts import STUDY_ANALYSIS_PROMPT
    from core.source_packs import get_source_pack
    pack = get_source_pack([], "")
    result = STUDY_ANALYSIS_PROMPT.format(
        title="T", author="A", duration="50:00",
        format_name="sermon", hermeneutic_method="expository",
        main_topic="t", analysis_summary="a", argument_arc="b",
        key_categories="k", timestamps="0:00 t",
        concepts="", scripture="", translations="", lexicon_notes="",
        synopsis_context="", source_pack=pack,
    )
    assert "{source_pack}" not in result


# ─── telegraph_pages.py ───────────────────────────────────────────────────────

def test_telegraph_pages_imports_source_packs():
    src = open("services/telegraph_pages.py", encoding="utf-8").read()
    assert "from core.source_packs import get_source_pack_for_ai_data" in src, \
        "telegraph_pages must import get_source_pack_for_ai_data"


def test_telegraph_pages_passes_source_pack_to_format():
    src = open("services/telegraph_pages.py", encoding="utf-8").read()
    assert "source_pack=_source_pack," in src, \
        "telegraph_pages must pass source_pack= to STUDY_ANALYSIS_PROMPT.format()"


def test_telegraph_pages_computes_source_pack():
    src = open("services/telegraph_pages.py", encoding="utf-8").read()
    assert "_source_pack = get_source_pack_for_ai_data(_ai)" in src, \
        "telegraph_pages must call get_source_pack_for_ai_data before format()"


# ─── source_packs.py full coverage ───────────────────────────────────────────

def test_source_packs_all_packs_non_empty():
    from core.source_packs import _PACKS
    for topic, sources in _PACKS.items():
        assert len(sources) >= 3, f"Pack '{topic}' has fewer than 3 sources"
        for s in sources:
            assert isinstance(s, str) and len(s) > 3, \
                f"Source '{s}' in pack '{topic}' is invalid"


def test_source_packs_keyword_mapping_coverage():
    """Every keyword must map to a valid pack."""
    from core.source_packs import _KEYWORD_TO_TOPIC, _PACKS
    for kw, topic in _KEYWORD_TO_TOPIC.items():
        assert topic in _PACKS, \
            f"Keyword '{kw}' maps to unknown topic '{topic}'"

