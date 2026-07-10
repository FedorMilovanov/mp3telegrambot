"""Regression tests for v3 patch 75 — closing re-check gaps."""

from converters.md_telegraph import _postprocess_telegraph_nodes, _section_to_nodes_v2
from core.content_audit import audit_expanded_sections
from core.generated_pages import build_generated_page_record
from core.json_parser import _parse_gemini_response
from core.source_titles import normalize_source_card_line
from core.synopsis_quality import audit_synopsis_density, synopsis_density_score
from core.text_utils import normalize_common_typos
from core.title_topic_audit import choose_safe_public_title


def _flat(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "".join(_flat(c) for c in node.get("children", []))
    if isinstance(node, list):
        return "".join(_flat(c) for c in node)
    return ""


def test_json_parser_rejects_obvious_timestamps_beyond_duration_plus_small_tolerance():
    raw = '{"real_author":"A","real_title":"T","format":"sermon","main_topic":"M","timestamps":[{"time":"10:20","topic":"ok"},{"time":"11:00","topic":"too far"}]}'
    parsed = _parse_gemini_response(raw, duration=630)
    assert parsed is not None
    assert "10:20 ok" in parsed["timestamps"]
    assert "11:00 too far" not in parsed["timestamps"]


def test_content_audit_no_legacy_duplicate_normalized_text_issues():
    _, _, issues = audit_expanded_sections(
        [{"title": "Странный огонь", "content": "Стину Лоусону. цитата / / Вторая глава"}],
        label="StudyAnalysis",
    )
    codes = [i.code for i in issues]
    assert "typo_fixed" in codes
    assert "separator_fixed" in codes
    assert "normalized_text" not in codes


def test_structured_scripture_quote_removes_only_one_outer_quote_pair():
    section = {
        "title": "T",
        "content": "fallback",
        "blocks": [{"type": "scripture", "ref": "Ин. 6:37", "quote": "«Он сказал: «придет»»"}],
    }
    text = _flat(_section_to_nodes_v2(section))
    assert "«Он сказал: «придет»»" in text


def test_synopsis_quality_prefers_blocks_over_content_fallback_not_double_count():
    section = {
        "title": "T",
        "time": "0:00",
        "content": "x" * 10000,
        "blocks": [{"type": "paragraph", "text": "short"}],
    }
    issues = audit_synopsis_density([section], duration_seconds=3600)
    assert any(i.code == "synopsis_too_few_chars" for i in issues)
    assert synopsis_density_score([section], 3600) < 1000


def test_source_card_legacy_dedupes_cyrillic_latin_duplicate_parenthetical():
    line = "• Джон МакАртур, Strange Fire (John MacArthur, Strange Fire)."
    assert normalize_source_card_line(line) == "**Чуждый огонь**, Джон МакАртур (Strange Fire, John MacArthur)."


def test_safe_public_title_uses_fallback_only_when_it_is_better_aligned():
    ai = {
        "real_title": "Святой Дух Откровения",
        "main_topic": "Пастырская верность и церковная дисциплина.",
        "timestamps": "0:00 Пастырская верность\n10:00 Церковная дисциплина",
        "title_topic_warning": "low overlap",
    }
    assert choose_safe_public_title(ai, "Моя ручная обложка") == "Святой Дух Откровения"
    assert choose_safe_public_title(ai, "Пастырская верность и церковная дисциплина") == "Пастырская верность и церковная дисциплина"


def test_archive_url_fields_are_not_truncated_at_800_chars():
    long_url = "https://example.com/" + "a" * 1500
    record = build_generated_page_record(video_id="longurl", source_url=long_url, title="T", synopsis_url=long_url)
    assert record["source_url"] == long_url
    assert record["synopsis_url"] == long_url


def test_postprocess_sentence_spacing_does_not_split_numeric_acronym_refs():
    nodes = [{"tag": "p", "children": ["NA28.BHS Ин.3:16.Следующее"]}]
    text = _postprocess_telegraph_nodes(nodes)[0]["children"][0]
    assert "NA28.BHS" in text
    assert "Ин.3:16" in text
    assert "16. Следующее" in text


def test_normalize_common_typos_can_skip_source_map_pass_for_audit_pipeline():
    src = "• Джон МакАртур, Чуждый огонь (John MacArthur, Strange Fire)."
    assert "John MacArthur" in normalize_common_typos(src, source_map=False)
    assert "Strange Fire, John MacArthur" in normalize_common_typos(src)
