"""Regression tests for v3 patch 31 — structured output for expanded pages."""

from pathlib import Path

from core.candidate_schema import expanded_page_response_schema, structured_json_config_kwargs
from services.telegraph_pages import _parse_expanded_json


def test_expanded_page_response_schema_contract():
    schema = expanded_page_response_schema()
    assert schema["type"] == "object"
    assert schema["required"] == ["outline", "sections"]
    outline_item = schema["properties"]["outline"]["items"]
    section_item = schema["properties"]["sections"]["items"]
    assert outline_item["required"] == ["title", "time"]
    assert section_item["required"] == ["title", "time", "content"]


def test_expanded_schema_works_with_structured_config_kwargs():
    kwargs = structured_json_config_kwargs(expanded_page_response_schema())
    assert kwargs["response_mime_type"] == "application/json"
    assert kwargs["response_schema"]["properties"]["sections"]["items"]["properties"]["content"]["type"] == "string"


def test_parse_expanded_json_accepts_schema_shape():
    raw = '{"outline":[{"title":"A","time":""}],"sections":[{"title":"A","time":"","content":"Text"}]}'
    parsed = _parse_expanded_json(raw)
    assert parsed is not None
    outline, sections = parsed
    assert outline[0]["title"] == "A"
    assert sections[0]["content"] == "Text"


def test_telegraph_pages_wires_expanded_structured_output_with_legacy_fallback():
    src = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert "from core.candidate_schema import expanded_page_response_schema" in src
    assert "def _expanded_structured_output_enabled" in src
    assert "EXPANDED_PAGES_STRUCTURED" in src
    assert "_structured_schema = expanded_page_response_schema()" in src
    assert "response_mime_type=(\"application/json\" if _structured_schema else None)" in src
    assert "response_schema=_structured_schema" in src
    assert "structured output failed" in src
    assert "retry legacy JSON config" in src


def test_expanded_structured_output_preserves_quota_overload_fallback_semantics():
    src = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert "is_quota_error(_schema_err)" in src
    assert "is_overload_error(_schema_err)" in src
    assert "_is_internal_error(_schema_err)" in src
    start = src.find("if response_schema is None or is_quota_error(_schema_err)")
    end = src.find("logger.warning", start)
    assert "raise" in src[start:end]
