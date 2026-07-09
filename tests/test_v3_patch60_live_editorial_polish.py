"""Regression tests for v3 patch 60 — live editorial polish."""

import json
from pathlib import Path

from converters.md_telegraph import _postprocess_telegraph_nodes
from core.json_parser import _parse_gemini_response
from core.person_names import normalize_person_names
from core.source_titles import normalize_source_card_line
from core.text_utils import normalize_common_typos, normalize_hashtag, scrub_third_person_phrases
from core.title_topic_audit import audit_title_topic_consistency


def _flat(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "".join(_flat(c) for c in node.get("children", []))
    if isinstance(node, list):
        return "".join(_flat(c) for c in node)
    return ""


def test_person_name_registry_normalizes_live_variants():
    text = "Ар Си Спраул, Р. Ч. Спрол, Эс Льюиса Джонсона и Мартина Лойда Джонса"
    out = normalize_person_names(text)
    assert "Р. Ч. Спроул" in out
    assert "С. Льюиса Джонсона" in out
    # AUDIT R22: выровнено с KNOWN_AUTHOR_RU ("Мартин Лойд-Джонс", одна "л") —
    # раньше эта таблица независимо использовала "Ллойд-Джонс" (см.
    # core/person_names.py::_PERSON_REPLACEMENTS), и один и тот же человек
    # получал разное написание в зависимости от того, какая функция его
    # обработала.
    assert "Мартина Лойд-Джонса" in out


def test_third_person_scrubber_handles_new_verbs():
    text = "Джон МакАртур подробно разворачивает этот смысл, что ученичество требует верности."
    out = scrub_third_person_phrases(text)
    assert "МакАртур" not in out
    assert "Ученичество требует верности" in out


def test_source_card_duplicate_cyrillic_parenthetical_is_removed():
    line = "• Джон МакАртур, The Master's Plan for the Church (Джон МакАртур, The Master's Plan for the Church.)"
    assert normalize_source_card_line(line) == "• Джон МакАртур, The Master's Plan for the Church."


def test_telegraph_spacing_polish_fixes_glued_source_text():
    nodes = [{"tag": "p", "children": ["-Достаточность в работеFaith Alone.Глубокий анализ в труде«Учение». Сперджен,* «Лекции»*"]}]
    out = _postprocess_telegraph_nodes(nodes)
    flat = _flat(out)
    assert "- Достаточность" in flat
    assert "работе Faith" in flat
    assert "Alone. Глубокий" in flat
    assert "труде «Учение»" in flat
    assert "Сперджен," in flat
    assert "*" not in flat


def test_title_topic_audit_flags_low_overlap_and_parser_records_warning():
    issue = audit_title_topic_consistency(
        "Святой Дух Откровения",
        "Пастырская верность, церковная дисциплина и библейская экклезиология",
        "0:00 церковь\n5:00 дисциплина",
    )
    assert issue is not None
    data = {
        "real_author": "A", "real_title": "Святой Дух Откровения", "format": "sermon",
        "main_topic": "Пастырская верность и церковная дисциплина.",
        "timestamps": [{"time": "0:00", "topic": "Библейская экклезиология"}],
        "hashtags": [], "analysis_summary": "a", "argument_arc": "b", "key_categories": [], "questions": [],
        "terms_data": {"concepts": [], "scripture": [], "translations": [], "lexicon_notes": []},
        "whisper_hints": [], "hermeneutic_method": "mixed",
    }
    parsed = _parse_gemini_response(json.dumps(data, ensure_ascii=False), duration=100)
    assert parsed and parsed.get("title_topic_warning")


def test_hashtag_canonical_registry():
    assert normalize_hashtag("БиблейскоеСемейство") == "#БиблейскаяСемья"
    assert normalize_hashtag("Богомыслие") == "#Богословие"


def test_content_audit_wires_expected_author_and_translation_warning():
    src = Path("core/content_audit.py").read_text(encoding="utf-8")
    assert "first_person_author_fixed" in src
    assert "mismatched first-person author" in src
    assert "translation_semantic_warning" in src or "evil" in src


def test_third_person_scrubber_removes_how_wrapper_from_reflection():
    text = "Лектор показывает, как легко подменить подлинную верность внешними атрибутами."
    out = scrub_third_person_phrases(text)
    assert "Лектор показывает" not in out
    assert "Легко подменить подлинную верность" in out


def test_third_person_scrubber_handles_author_surnames_from_live_pages():
    text = "Данкан подчеркивает этот смысловой пласт, что поклонение должно быть в духе и истине."
    out = scrub_third_person_phrases(text)
    assert "Данкан подчеркивает" not in out
    assert "Поклонение должно быть" in out


def test_postprocess_repairs_split_hyphenated_term_and_dannyy_source_wording():
    nodes = [{"tag": "p", "children": [
        "• ", {"tag": "b", "children": ["Историко"]},
        " — грамматический метод. Данный академический труд исследует тему."
    ]}]
    out = _postprocess_telegraph_nodes(nodes)
    flat = _flat(out)
    assert "Историко-грамматический метод" in flat
    assert "Данный академический" not in flat
    assert "Академический труд" in flat


def test_common_typos_repair_mixed_english_day_in_scripture_quote():
    assert normalize_common_typos("И был вечер, и было утро: day один") == "И был вечер, и было утро: день один"
