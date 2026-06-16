"""Regression tests for v3 patch 26 — live-run caption/search/audit polish."""

from pathlib import Path

from core.page_audit import audit_telegraph_page
from services.search import _best_match


def test_page_audit_does_not_flag_translation_forks_as_source_titles():
    nodes = [
        {"tag": "h3", "children": ["Переводческие развилки"]},
        {"tag": "p", "children": [
            "Псалом 18:8 — «укрепляет душу» vs «restoring the soul» / «reversing the soul». "
            "Синодальный перевод сглаживает динамический характер оригинала."
        ]},
        {"tag": "p", "children": [
            "Псалом 18:9 — «заповедь светла» vs «pure» / «radiant». "
            "Это не библиографическая карточка источника."
        ]},
    ]
    issues = audit_telegraph_page("T", nodes, page_type="study")
    assert "source_map_original_title" not in {i.code for i in issues}


def test_page_audit_still_flags_source_card_in_source_map_context():
    nodes = [
        {"tag": "h3", "children": ["Карта источников для дальнейшего изучения"]},
        {"tag": "p", "children": ["• Джон МакАртур, Странный огонь (John MacArthur, Strange Fire)."]},
    ]
    issues = audit_telegraph_page("T", nodes, page_type="study")
    assert "source_map_original_title" in {i.code for i in issues}


def test_search_scoring_does_not_match_by_author_and_duration_only():
    title = "Достаточность Писания: Псалом 18 - Джон МакАртур"
    results = [
        {"id": "wrong", "title": "Радостные рабы Христовы (Джон МакАртур)", "duration": 3393},
        {"id": "right", "title": "2017 G3 Conference | 6 | Достаточность Писания: Псалом 18", "duration": 3379},
    ]
    url, score = _best_match(results, title, 3403, "rutube", early_exit=False, log=False)
    assert url == "https://rutube.ru/video/right/"
    assert score > 0.4


def test_caption_trimming_has_compact_timestamp_fallback():
    src = Path("pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "for _mini_limit in (7, 5, 3):" in src
    assert '"main_topic": ""' in src
    assert "ts_mini = _trim_timestamps" in src
    assert "ts_in_cap=0" in src  # logging remains for cases where even 3 timestamps cannot fit


def test_page_audit_does_not_flag_doctrinal_concept_with_latin_parenthetical_as_source():
    nodes = [
        {"tag": "h3", "children": ["Карта источников"]},
        {"tag": "p", "children": [
            "• Спасение только по благодати, только через веру, только во Христа "
            "(Sola Gratia, Sola Fide, Solus Christus) — реформатский принцип, "
            "утверждающий, что спасение не основано на заслугах человека."
        ]},
    ]
    issues = audit_telegraph_page("T", nodes, page_type="study")
    assert "source_map_original_title" not in {i.code for i in issues}


def test_page_audit_allows_registered_official_ru_source_title():
    nodes = [
        {"tag": "h3", "children": ["Карта источников"]},
        {"tag": "p", "children": [
            "• Наставление в христианской вере, Жан Кальвин "
            "(Institutes of the Christian Religion, John Calvin)."
        ]},
    ]
    issues = audit_telegraph_page("T", nodes, page_type="study")
    assert "source_map_original_title" not in {i.code for i in issues}
