"""Regression tests for v3 patch 33 — Synopsis structured output + positive reasoning."""

from pathlib import Path

from core.candidate_schema import expanded_page_response_schema
from services.telegraph_pages import _parse_expanded_json


def test_synopsis_can_use_expanded_page_schema_shape():
    schema = expanded_page_response_schema()
    assert schema["required"] == ["outline", "sections"]
    raw = '{"outline":[{"title":"A","time":"0:00"}],"sections":[{"title":"A","time":"0:00","content":"Text"}]}'
    outline, sections = _parse_expanded_json(raw)
    assert outline[0]["time"] == "0:00"
    assert sections[0]["content"] == "Text"


def test_telegraph_synopsis_wires_structured_output_with_legacy_fallback():
    src = Path("services/telegraph.py").read_text(encoding="utf-8")
    assert "from core.candidate_schema import expanded_page_response_schema" in src
    assert "def _synopsis_structured_output_enabled" in src
    assert "SYNOPSIS_STRUCTURED" in src
    assert 'os.getenv("SYNOPSIS_STRUCTURED", "0")' in src
    assert "response_mime_type=\"application/json\"" in src
    assert "response_schema=expanded_page_response_schema()" in src
    assert "Synopsis structured output failed" in src
    assert "retry legacy JSON config" in src
    assert "return await _generate_synopsis_content(" in src
    assert "client, GEMINI_MODEL" in src
    assert "gemini-2.5-flash-lite" in src


def test_synopsis_structured_output_preserves_quota_overload_passthrough():
    src = Path("services/telegraph.py").read_text(encoding="utf-8")
    assert "is_quota_error(_schema_err)" in src
    assert "is_overload_error(_schema_err)" in src
    start = src.find("if is_quota_error(_schema_err) or is_overload_error(_schema_err):")
    end = src.find("logger.warning", start)
    assert "raise" in src[start:end]


def test_synopsis_positive_reasoning_note_is_injected_into_qa_and_v2():
    src = Path("services/telegraph.py").read_text(encoding="utf-8")
    assert "SYNOPSIS_REASONING_FIRST_NOTE" in src
    from core.reasoning_guidance import build_synopsis_reasoning_note
    note = build_synopsis_reasoning_note()
    assert "РАЗРЕШЕНО И ЖЕЛАТЕЛЬНО" in note
    assert "ход речи автора" in note
    assert "СТЕПЕНЬ ДОСЛОВНОСТИ КОНСПЕКТА" in note
    assert "сжатая стенограмма" in note
    assert 'prompt = SYNOPSIS_REASONING_FIRST_NOTE + "\\n\\n" + _qa_prompt_template.format' in src
    assert "_syn_format_note =" in src
    assert 'SYNOPSIS_REASONING_FIRST_NOTE + "\\n" + _format_note' in src
    assert "_verbatim_context" in src and "_primary_context" in src
