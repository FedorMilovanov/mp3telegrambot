"""Regression tests for v3 patch 79 — live quality follow-up."""

from pathlib import Path

from converters.md_telegraph import _section_to_nodes_v2
from core.content_audit import audit_expanded_sections
from services.telegraph_pages import create_telegraph_study_reflection_combined


def _flat(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "".join(_flat(c) for c in node.get("children", []))
    if isinstance(node, list):
        return "".join(_flat(c) for c in node)
    return ""


def test_navigation_block_uses_space_outside_bold_label():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert '"children": [{"tag": "b", "children": ["📂 Читать также:"]}, " "] + nav_children' in src
    assert '"📂 Читать также: "]}] + nav_children' not in src


def test_combined_pages_publish_with_page_prefix_and_quality_size_guard():
    src = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert "COMBINED_STUDY_REFLECTION_MAX_PROMPT_CHARS" in src
    assert "prompt too large for quality-first mode" in src
    assert "full_page_title = f\"{page_prefix}{_sep}{tg_title}\"" in src
    assert "full_page_title," in src


def test_content_audit_removes_channel_position_and_material_wording():
    sections = [{
        "title": "Лексика",
        "content": (
            "Канал занимает позицию цессационизма, основываясь на реформатской традиции. "
            "В материале этот термин рассматривается в контексте проверки пророчества. "
            "Материал критикует ошибочное пророчество."
        ),
    }]
    out, _, issues = audit_expanded_sections(sections, label="StudyAnalysis")
    text = out[0]["content"]
    assert "Канал занимает" not in text
    assert "В материале" not in text
    assert "Этот термин работает" in text
    # AUDIT R6: безличная форма вместо запрещённой обёртки «Автор критикует»
    assert "Автор критикует" not in text
    assert "Критикуется" in text
    assert any(i.code == "prompt_context_leak_fixed" for i in issues)


def test_structured_block_aliases_render_without_schema_warnings():
    section = {
        "title": "T",
        "content": "fallback",
        "blocks": [
            {"type": "quote", "quote": "«Проверьте пророков»"},
            {"type": "lexical_analysis", "lemma": "προφητεία", "role_in_argument": "Показывает стандарт истинного пророчества."},
            {"type": "theological_line", "title_original": "Цессационистский аргумент", "why_relevant": "Показывает достаточность Писания."},
            {"type": "scripture", "ref": "Втор. 18:22", "quote": "если слово не сбудется", "role_in_argument": "Текст задаёт критерий проверки."},
        ],
    }
    out, _, issues = audit_expanded_sections([section], label="StudyAnalysis")
    assert not any(i.code == "block_schema_warning" for i in issues)
    flat = _flat(_section_to_nodes_v2(out[0]))
    assert "Проверьте пророков" in flat
    assert "προφητεία" in flat
    assert "Цессационистский аргумент" in flat
    assert "Текст задаёт критерий проверки" in flat


def test_combined_function_exists_but_docstring_says_quality_first_opt_in():
    assert create_telegraph_study_reflection_combined.__name__ == "create_telegraph_study_reflection_combined"
    doc = create_telegraph_study_reflection_combined.__doc__ or ""
    assert "Optional" in doc or "Disabled by default" in doc


def test_prompts_ban_channel_position_and_bare_key_texts():
    src = Path("core/prompts.py").read_text(encoding="utf-8")
    assert "Никогда не публикуй внутреннюю редакционную рамку" in src
    assert "нельзя давать голую подборку стихов" in src
    assert "не ограничивайся словарным значением" in src


def test_error_heading_cross_is_moved_after_full_heading():
    from converters.md_telegraph import _postprocess_telegraph_nodes
    nodes = [{"tag": "p", "children": ["**Внутренний свет** ❌ **как высший авторитет .**"]}]
    flat = _flat(_postprocess_telegraph_nodes(nodes))
    assert "Внутренний свет" in flat
    assert "❌" in flat
    assert "Подмена: как высший авторитет." in flat
    assert not flat.strip().endswith("❌")


def test_source_cards_are_title_first_with_original_parenthetical():
    from core.source_titles import normalize_source_card_line
    assert normalize_source_card_line("• John MacArthur, Strange Fire") == (
        "**Чуждый огонь**, Джон МакАртур (Strange Fire, John MacArthur)."
    )



def test_scripture_text_only_block_is_not_lost_or_warned():
    from core.content_audit import audit_expanded_sections
    section = {
        "title": "Ключевые тексты",
        "content": "fallback",
        "blocks": [{"type": "scripture", "text": "**2 Тимофею 3:16:** *«Всё Писание богодухновенно»*\n\nЭтот текст задаёт стандарт авторитета."}],
    }
    out, _, issues = audit_expanded_sections([section], label="StudyAnalysis")
    assert not any(i.code == "block_schema_warning" for i in issues)
    flat = _flat(_section_to_nodes_v2(out[0]))
    assert "2 Тимофею 3:16" in flat
    assert "Этот текст задаёт стандарт" in flat


def test_prompts_require_cross_in_middle_not_end():
    src = Path("core/prompts.py").read_text(encoding="utf-8")
    assert "Красный крест НЕ ставь" in src
    assert "**Тема** ❌ **Подмена:" in src


def test_synopsis_prompt_demands_transcript_like_fidelity_and_inline_anchors():
    src = Path("core/prompts.py").read_text(encoding="utf-8")
    assert "РЕЖИМ 100% ПОДРОБНОЙ ТРАНСКРИПЦИИ (АБСОЛЮТНО БЕЗ СЖАТИЯ)" in src
    assert "Джоне Роджерсе" in src
    assert "2000 изгнанных пасторах" in src
    assert "inline-якоря ⏱" in src
    assert "нормально, если Telegraph\nполучится в 2 части" in src


def test_structured_schema_has_argument_and_application_blocks():
    from core.candidate_schema import expanded_page_response_schema
    schema = expanded_page_response_schema()
    enum = schema["properties"]["sections"]["items"]["properties"]["blocks"]["items"]["properties"]["type"]["enum"]
    for block_type in ("thesis", "argument_spine", "pull_quote", "application"):
        assert block_type in enum
    props = schema["properties"]["sections"]["items"]["properties"]["blocks"]["items"]["properties"]
    for key in ("steps", "challenge", "anchor_timestamp", "concrete_step"):
        assert key in props
    # R46: удалено насовсем — не должно оставаться и в схеме
    assert "common_misreading" not in props


def test_synopsis_density_audit_requires_inline_anchors_for_long_transcripts():
    from core.synopsis_quality import audit_synopsis_density, get_synopsis_density_profile
    assert get_synopsis_density_profile(3600).min_total_chars >= 8500
    sections = [{"title": f"S{i}", "time": f"{i * 5}:00", "content": "x" * 1000} for i in range(10)]
    issues = audit_synopsis_density(sections, 3600)
    assert "synopsis_missing_inline_anchors" in {i.code for i in issues}


def test_new_structured_blocks_render_argument_spine_pull_quote_and_application():
    section = {
        "title": "T",
        "content": "fallback",
        "blocks": [
            {"type": "thesis", "text": "Писание достаточно."},
            {"type": "argument_spine", "steps": ["Реформация", "квакеры", "Оуэн"]},
            {"type": "pull_quote", "quote": "Это сердце Реформации", "timestamp": "12:34"},
            {"type": "application", "challenge": "Проверь внутренний голос", "anchor_timestamp": "52:00", "concrete_step": "Сверь решение с конкретным текстом Писания."},
        ],
    }
    flat = _flat(_section_to_nodes_v2(section))
    assert "Главная мысль" in flat
    assert "Ход мысли автора" in flat
    assert "Это сердце Реформации" in flat
    assert "Проверь внутренний голос" in flat
    assert "Сверь решение" in flat
