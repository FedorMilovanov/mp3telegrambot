"""AUDIT R12 (2026-07-06, вычитка дампов страниц после прогона плейлиста).

Находки:
1. Q&A-страницы: «…на бумаге. [⏱ 0:15](url).» — правило «точка после
   таймкода» обязано работать на markdown-стадии ДО линкификации: после
   неё таймкод разрезан на <a>-узел и строковые правила бессильны.
2. Карточки Разбора шли через blocks-путь МИМО зачистки кружков.
3. Литеральный «\\n\\n» стоит на месте разрыва абзаца (после жирного
   вопроса) — должен становиться НАСТОЯЩИМ переносом, а не пробелом.
4. Промт Reflection сам предписывал «• **От…к….** Ибо/Ведь/Поскольку» —
   кружки убраны из шаблонов, зачины обязаны различаться.
"""
from pathlib import Path

from converters.md_telegraph import (
    _fix_ts_period_order,
    _section_to_nodes_v2,
    _structured_blocks_to_nodes_v2,
)

ROOT = Path(__file__).resolve().parents[1]


def _flat(nodes) -> str:
    parts = []
    def walk(n):
        if isinstance(n, str):
            parts.append(n)
        elif isinstance(n, dict):
            for c in n.get("children", []) or []:
                walk(c)
    for n in nodes:
        walk(n)
    return "".join(parts)


# ── 1. Точка после таймкода — на markdown-стадии ────────────────

def test_fix_ts_period_order_unit():
    f = _fix_ts_period_order
    assert f("менее духовными. ⏱ 0:15.") == "менее духовными ⏱ 0:15."
    assert f("бумаге. ⏱ 0:40") == "бумаге ⏱ 0:40."
    assert f("абзац. ⏱ 2:10.\n\nНовый абзац.") == "абзац ⏱ 2:10.\n\nНовый абзац."
    assert f("мысли. ⏱ 3:30 Следующая мысль") == "мысли ⏱ 3:30. Следующая мысль"
    # уже правильный порядок не трогаем
    assert f("Духа ⏱ 11:29. Дальше") == "Духа ⏱ 11:29. Дальше"


def test_qa_paragraph_period_fixed_before_linkify():
    """Сквозной сценарий Q&A: после линкификации точка стоит ПОСЛЕ ссылки,
    а перед таймкодом точки нет."""
    nodes = _section_to_nodes_v2(
        {"title": "Вопрос о ручке", "time": "",
         "content": "Многие чувствуют себя менее духовными. ⏱ 0:15.\n\nВторой абзац мысли. ⏱ 0:40."},
        yt_url="https://www.youtube.com/watch?v=x", duration=600,
    )
    flat = _flat(nodes)
    assert "духовными ⏱ 0:15." in flat.replace(" ", " ")
    assert ". ⏱" not in flat, f"точка перед таймкодом уцелела: {flat!r}"


def test_section_content_path_calls_shared_helpers():
    src = (ROOT / "converters/md_telegraph.py").read_text(encoding="utf-8")
    sect = src.split("def _section_to_nodes_v2", 1)[1][:4000]
    assert "_strip_card_bullets(" in sect
    assert "_fix_ts_period_order(content)" in sect


# ── 2. Blocks-путь: зачистки и карточки без «•» ─────────────────

def test_blocks_path_lexicon_card_without_bullet():
    nodes = _structured_blocks_to_nodes_v2([
        {"type": "lexicon", "lemma": "ebed (עֶבֶד, евр.)",
         "role_in_argument": "закрывает ложный вывод о слабости Раба"},
    ])
    flat = _flat(nodes)
    assert "ebed" in flat
    assert "•" not in flat, "лексическая карточка не должна иметь кружок"


def test_blocks_path_source_card_keeps_bullet():
    nodes = _structured_blocks_to_nodes_v2([
        {"type": "source", "author": "Джон Оуэн",
         "title_original": "The Death of Death in the Death of Christ",
         "why_relevant": "классическая работа об искуплении"},
    ])
    flat = _flat(nodes)
    assert "•" in flat, "карточка источника сохраняет кружок"


def test_blocks_path_fixes_ts_period():
    nodes = _structured_blocks_to_nodes_v2([
        {"type": "paragraph", "text": "Служители молятся мало. ⏱ 2:29."},
    ])
    flat = _flat(nodes)
    assert "мало ⏱ 2:29." in flat
    assert ". ⏱" not in flat


# ── 3. Литеральный \n\n → настоящий разрыв абзаца ───────────────

def test_blocks_literal_backslash_n_becomes_paragraph_break():
    nodes = _structured_blocks_to_nodes_v2([
        {"type": "paragraph",
         "text": "**Когда ты жертвовал сном ради молитвы?** \\n\\nИисус встал до рассвета."},
    ])
    p_nodes = [n for n in nodes if isinstance(n, dict) and n.get("tag") == "p"]
    assert len(p_nodes) >= 2, "литеральный \\n\\n должен разорвать абзац, а не склеить"
    flat = _flat(nodes)
    assert "\\n" not in flat


# ── 4. Промты: кружки убраны из шаблонов, зачины разнообразны ───

def test_reflection_prompt_no_bullet_card_templates():
    from core.prompts import REFLECTION_APPLICATION_PROMPT as p
    assert "• **От" not in p, "промт снова предписывает кружки у карточек"
    assert "Ибо / Ведь / Поскольку" not in p
    assert "РАЗНООБРАЗИЕ ЗАЧИНОВ" in p


def test_study_prompt_no_bullet_definition_template():
    from core.prompts import STUDY_ANALYSIS_PROMPT as p
    assert "• **Термин / понятие**" not in p
    # scripture-шаблоны с кружком остаются — это осознанное правило
    assert "• **Луки 18:15" in p or "• **Матфея 7:21" in p


# ── 5. Дубль автора в скобках у карточек источников ─────────────

def test_source_card_author_not_duplicated_in_parens():
    from core.source_titles import build_source_card, render_source_card
    from converters.md_telegraph import _final_telegraph_polish

    card = build_source_card(
        author="Джон МакАртур",
        title_original="The Gospel According to God",
    )
    rendered = render_source_card(card)
    assert rendered.count("Джон МакАртур") <= 1 or "(" not in rendered.split("Джон МакАртур", 1)[1], (
        f"дубль автора в скобках: {rendered!r}"
    )

    # финальный рубеж: «Имя (Имя)» схлопывается в полировке
    nodes = _final_telegraph_polish(
        [{"tag": "p", "children": ["**Title**, Джон МакАртур (Джон МакАртур)."]}]
    )
    flat = "".join(c for c in nodes[0]["children"] if isinstance(c, str))
    assert "(Джон МакАртур)" not in flat
