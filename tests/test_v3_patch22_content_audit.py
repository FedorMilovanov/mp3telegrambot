"""Regression tests for v3 patch 22 — section-level content audit."""

from pathlib import Path

from core.content_audit import audit_expanded_sections, format_content_audit_issues


def test_content_audit_normalizes_section_title_content_and_outline():
    sections = [
        {
            "title": "Странный огонь и авторитет Слово Божьего",
            "time": "0:00",
            "content": "Джон МакАртур подчеркивает этот аспект, говоря о том, что верность важна. Стину Лоусону.",
        }
    ]
    outline = [{"title": "Странный огонь", "time": "0:00"}]

    out_sections, out_outline, issues = audit_expanded_sections(sections, outline, label="StudyAnalysis")

    assert out_sections[0]["title"] == "Чуждый огонь и авторитет Слова Божьего"
    # Study pages now SCRUB third-person analytical framing
    assert "Джон МакАртур подчеркивает" not in out_sections[0]["content"]
    assert "Стиву Лоусону" in out_sections[0]["content"]
    assert out_outline[0]["title"] == "Чуждый огонь"
    codes = {i.code for i in issues}
    assert "typo_fixed" in codes
    # third_person IS scrubbed for Study — third_person_fixed issue is present
    assert "third_person_fixed" in codes


def test_content_audit_source_map_prefers_original_and_dedupes_authors():
    sections = [
        {
            "title": "Карта источников для дальнейшего изучения",
            "content": (
                "• Джон МакАртур, Чуждый огонь (John MacArthur, Strange Fire).\n"
                "• Кевин ДеЯнг, Грег Гилберт, Greg Gilbert, What Is the Mission of the Church?."
            ),
        }
    ]

    out_sections, _, issues = audit_expanded_sections(sections, [], label="StudyAnalysis")
    content = out_sections[0]["content"]

    assert "Чуждый огонь (John MacArthur" not in content
    assert "**Чуждый огонь**, Джон МакАртур (Strange Fire, John MacArthur)." in content
    assert "Грег Гилберт, Greg Gilbert" not in content
    assert "Кевин ДеЯнг и Грег Гилберт" in content
    assert any(i.code == "source_card_fixed" for i in issues)


def test_content_audit_warns_about_mixed_greek_after_known_fixes():
    sections = [{"title": "Лексика", "content": "Слово αβвγ остается смешанным."}]
    out_sections, _, issues = audit_expanded_sections(sections, [], label="StudyAnalysis")
    assert out_sections[0]["content"] == "Слово αβвγ остается смешанным."
    assert any(i.code == "mixed_greek_cyrillic_warning" for i in issues)


def test_content_audit_warns_about_bare_translation_forks():
    sections = [
        {
            "title": "Переводческие развилки",
            "content": "• Луки 11:2: «Отче наш» vs «Отче»\n• Луки 11:3: «Хлеб наш насущный»",
        }
    ]
    _, _, issues = audit_expanded_sections(sections, [], label="StudyAnalysis")
    assert any(i.code == "bare_translation_forks_warning" for i in issues)
    rendered = format_content_audit_issues(issues)
    assert "bare_translation_forks_warning" in rendered


def test_content_audit_is_wired_into_expanded_and_synopsis_pipelines():
    telegraph_pages = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    telegraph = Path("services/telegraph.py").read_text(encoding="utf-8")

    assert "from core.content_audit import audit_expanded_sections" in telegraph_pages
    assert "sections, outline, _content_audit = audit_expanded_sections(" in telegraph_pages
    assert "content audit before publish" in telegraph_pages

    assert "from core.content_audit import audit_expanded_sections" in telegraph
    assert "sections, outline, _content_audit = audit_expanded_sections(" in telegraph
    assert "Synopsis v2: content audit before publish" in telegraph


def test_content_audit_rewrites_dannyy_academic_source_wording():
    sections = [{"title": "Карта источников", "content": "Данный академический труд исследует тему."}]
    out, _, issues = audit_expanded_sections(sections, label="StudyAnalysis")
    assert "Данный академический" not in out[0]["content"]
    assert "Академический труд" in out[0]["content"]
    assert any(i.code == "prompt_context_leak_fixed" for i in issues)
