"""Regression tests for v3 patch 58 — coverage and author fidelity polish."""

from pathlib import Path

from core.content_audit import audit_expanded_sections
from core.prompts import build_audio_analysis_prompt


def test_content_audit_removes_mismatched_first_person_author():
    sections = [{
        "title": "A",
        "content": "Для меня, Алексея Коломийцева, огромная радость. Я, Коломийцев, убежден в этом.",
    }]
    out, _, issues = audit_expanded_sections(sections, [], label="Synopsis", expected_author="Алекс Монтойя")
    content = out[0]["content"]
    assert "Коломийцев" not in content
    assert "Для меня огромная радость" in content
    assert "Я убежден" in content
    assert any(i.code == "first_person_author_fixed" for i in issues)


def test_content_audit_keeps_matching_first_person_author():
    sections = [{"title": "A", "content": "Я, Алекс Монтойя, убежден в этом."}]
    out, _, issues = audit_expanded_sections(sections, [], label="Synopsis", expected_author="Алекс Монтойя")
    assert "Я, Алекс Монтойя" in out[0]["content"]
    assert not any(i.code == "first_person_author_fixed" for i in issues)


def test_audio_prompt_has_concrete_final_timestamp_floor():
    prompt = build_audio_analysis_prompt("T", "A", "55:00", 3300, mode="deep")
    assert "примерно не раньше" in prompt
    assert "48:24" in prompt  # 3300 * 0.88
    assert "ОБЯЗАТЕЛЬНО дай отдельный поздний таймкод" in prompt


def test_synopsis_passes_expected_author_to_content_audit():
    src = Path("services/telegraph.py").read_text(encoding="utf-8")
    assert "expected_author=author" in src
    assert "_ts_raw[:24]" in src
    assert "не обрывай конспект на первой половине" in src
