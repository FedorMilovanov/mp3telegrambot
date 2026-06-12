"""Regression tests for first-person author cleanup in content audit."""

from core.content_audit import audit_expanded_sections


def test_audit_does_not_mangle_biblical_i_the_lord_phrase():
    sections = [{
        "title": "Цитата пророка",
        "content": "Автор цитирует: «Я, Господь, говорю это» и затем объясняет смысл.",
    }]
    out, _, issues = audit_expanded_sections(sections, label="Synopsis", expected_author="Джон МакАртур")
    assert "Я, Господь" in out[0]["content"]
    assert not any(i.code == "first_person_author_fixed" for i in issues)


def test_audit_still_removes_mismatched_human_first_person_author():
    sections = [{"title": "x", "content": "Я, Пётр Иванов, считаю это важным."}]
    out, _, issues = audit_expanded_sections(sections, label="Synopsis", expected_author="Джон МакАртур")
    assert "Пётр Иванов" not in out[0]["content"]
    assert any(i.code == "first_person_author_fixed" for i in issues)
