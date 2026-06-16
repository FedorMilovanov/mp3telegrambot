"""Tests for v3 patch 20 — live-run typo polish + prompt guards.

Based on runs 2026-05-26 19:18–19:53:
- QA title typo: «доктлинальным» → «доктринальным»;
- body typo: «великими богологами» → «великими богословами»;
- body typo: «понимание Божьего Слово» → «понимание Божьего Слова»;
- translation-forks section must not emit bare headings without analysis;
- audio prompt final checklist must reject third-person summary fields.
"""

from converters.md_telegraph import _postprocess_telegraph_nodes
from core.text_utils import _scrub_inline, normalize_common_typos


def _flat(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "".join(_flat(c) for c in node.get("children", []))
    return ""


def test_normalize_common_typos_for_live_run_words():
    text = "доктлинальным основанием, великими богологами, понимание Божьего Слово"
    assert normalize_common_typos(text) == (
        "доктринальным основанием, великими богословами, понимание Божьего Слова"
    )


def test_scrub_inline_applies_common_typos():
    assert _scrub_inline("Искаженным доктлинальным основанием") == "Искаженным доктринальным основанием"
    assert _scrub_inline("создавались великими богологами") == "создавались великими богословами"


def test_telegraph_postprocess_fixes_typos_in_toc_and_titles():
    nodes = [
        {"tag": "p", "children": ["**6.** Искаженным доктлинальным основанием"]},
        {"tag": "h3", "children": ["Имеет ли смысл оставаться с доктлинальным основанием?"]},
        {"tag": "p", "children": ["Они создавались великими богологами."]},
        {"tag": "p", "children": ["Величайшее чудо — понимание Божьего Слово."]},
    ]
    out = _postprocess_telegraph_nodes(nodes)
    flat = "\n".join(_flat(n) for n in out)
    assert "доктлин" not in flat
    assert "боголог" not in flat
    assert "Божьего Слово" not in flat
    assert "доктринальным" in flat
    assert "богословами" in flat
    assert "Божьего Слова" in flat


def test_study_translation_prompt_forbids_bare_headings():
    from core.prompts import STUDY_ANALYSIS_PROMPT
    assert "НЕ ОСТАВЛЯЙ ГОЛЫЕ ЗАГОЛОВКИ" in STUDY_ANALYSIS_PROMPT
    assert "Отче наш" in STUDY_ANALYSIS_PROMPT
    assert "Хлеб наш насущный" in STUDY_ANALYSIS_PROMPT
    assert "полностью пропусти section TYPE 4" in STUDY_ANALYSIS_PROMPT


def test_audio_prompt_final_checklist_mentions_summary_fields_and_pompous_labels():
    from core.prompts import build_audio_analysis_prompt
    prompt = build_audio_analysis_prompt("T", "", "55:00", 3300, mode="deep")
    tail = prompt[prompt.find("ФИНАЛЬНАЯ САМОПРОВЕРКА"):]
    assert "analysis_summary" in tail
    assert "argument_arc" in tail
    assert "имя/роль автора" in tail
    assert "богословские гиганты" in tail


def test_audio_prompt_example_stays_neutral():
    from core.prompts import build_audio_analysis_prompt
    prompt = build_audio_analysis_prompt("T", "", "55:00", 3300, mode="deep")
    example_pos = prompt.find('"main_topic"')
    example = prompt[example_pos:example_pos + 260]
    assert "МакАртур задаёт" not in example
    assert "Он вскрывает" not in example


def test_normalize_common_typos_fixes_mixed_greek_cyrillic_terms():
    assert normalize_common_typos("βασιлеία") == "βασιλεία"
    assert normalize_common_typos("μορφύω и μεταμορφύω") == "μορφόω и μεταμορφόω"


def test_source_map_prefers_original_title_over_invented_russian_title():
    from converters.md_telegraph import _section_to_nodes_v2
    nodes = _section_to_nodes_v2({
        "title": "Карта источников для дальнейшего изучения",
        "content": "• Синклер Фергюсон, _«Искушение молитвой Господней»_ ( _Sinclair Ferguson, The Lord's Prayer_).\n\nОписание.",
    })
    card = next(n for n in nodes if isinstance(n, dict) and n.get("tag") == "p" and _flat(n).startswith("•"))
    flat = _flat(card)
    assert "Искушение молитвой Господней" not in flat
    assert "The Lord's Prayer" in flat
    assert "Sinclair Ferguson" not in flat  # English author duplicate is stripped from title
