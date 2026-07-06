"""AUDIT R11 (2026-07-06, живой прогон плейлиста Shepherds' Conference).

Находки оператора + аудит логов:
1. «предложение. ⏱ 11:29.» — точка с ОБЕИХ сторон таймкода, в т.ч. в
   середине абзаца (старый фиксер ловил только хвост строки).
2. Голые «**» на страницах Разбора/Размышления — _final_telegraph_polish
   вызывался только в пути Synopsis.
3. Литеральный «\\n» в опубликованном тексте.
4. «Лес кружков»: • перед длинными жирными шапками карточек и в заголовках.
5. Navigation логировала «добавлена» при CONTENT_TOO_BIG без fallback.
6. Промты: анти-вода/плотность мысли для Study и Reflection.
"""
from pathlib import Path

from converters.md_telegraph import _postprocess_telegraph_nodes

ROOT = Path(__file__).resolve().parents[1]


def _fix(text: str) -> str:
    out = _postprocess_telegraph_nodes([{"tag": "p", "children": [text]}])
    return out[0]["children"][0]


# ── 1. Точка только ПОСЛЕ таймкода, одна ────────────────────────

def test_double_period_around_timestamp_collapsed():
    assert _fix("к свободе. ⏱ 12:15.") == "к свободе ⏱ 12:15."


def test_mid_paragraph_period_before_timestamp_moved():
    got = _fix("духовная тьма. ⏱ 24:00. Новая мысль началась")
    assert got.startswith("духовная тьма ⏱ 24:00. Новая мысль")


def test_trailing_period_before_timestamp_moved():
    assert _fix("крушение рамок. ⏱ 6:16") == "крушение рамок ⏱ 6:16."


def test_period_before_timestamp_next_sentence():
    got = _fix("итог пути. ⏱ 40:40 Христос реален")
    assert "пути ⏱ 40:40. Христос реален" in got


def _flat(text: str) -> str:
    """Сплющивает результат (bold-узлы → текст) для проверки порядка точек."""
    out = _postprocess_telegraph_nodes([{"tag": "p", "children": [text]}])
    parts = []
    for ch in out[0]["children"]:
        if isinstance(ch, str):
            parts.append(ch)
        else:
            parts.append("".join(ch.get("children", [])))
    return "".join(parts)


def test_bold_timestamp_double_period():
    # bold разворачивается в <b>-узел; проверяем итоговый порядок символов
    assert _flat("сокрушение. ⏱ **11:29**.") == "сокрушение ⏱ 11:29."


def test_emoji_variation_selector_normalized():
    # ⏱️ (с U+FE0F) ломал все регэкспы — селектор не пробел и не цифра
    assert _fix("Духа. ⏱️ 11:29.") == "Духа ⏱ 11:29."


def test_correct_timestamp_untouched():
    assert _fix("Духа ⏱ 11:29. Дальше текст") == "Духа ⏱ 11:29. Дальше текст"


# ── 2-3. Литеральный \n и полировка на всех путях ───────────────

def test_literal_backslash_n_removed():
    assert _fix("первая мысль \\n вторая мысль") == "первая мысль вторая мысль"


def test_final_polish_applied_on_create_and_edit_paths():
    src = (ROOT / "converters/md_telegraph.py").read_text(encoding="utf-8")
    create_fn = src.split("async def _create_telegraph_page_single", 1)[1]
    create_head = create_fn.split("for attempt in range", 1)[0]
    assert "_final_telegraph_polish(nodes)" in create_head, (
        "страницы Study/Reflection публиковались без полировки — голые ** уходили в Telegraph"
    )
    edit_fn = src.split("async def _edit_telegraph_page", 1)[1]
    edit_head = edit_fn.split("for _edit_attempt", 1)[0]
    assert "_final_telegraph_polish(nodes)" in edit_head


# ── 4. Кружки: длинные жирные шапки и заголовки без • ───────────

def test_bullet_stripped_from_long_bold_card_headers():
    from converters.md_telegraph import _strip_card_bullets

    card = "• **От иллюзии земного благополучия к упованию.** Мы склонны"
    assert _strip_card_bullets(card).startswith("**От иллюзии")
    scripture = "• **Мф 7:21:** *«Не всякий, говорящий Мне»*"
    assert _strip_card_bullets(scripture) == scripture, "scripture-блок должен сохранить •"
    short_item = "• **Покаяние** и вера"
    assert _strip_card_bullets(short_item) == short_item, "короткий пункт сохраняет •"
    # R12: карточка источника «**Название**, Автор» — жирный + запятая — сохраняет •
    source = "• **Смерть смерти в смерти Христа**, Джон Оуэн (The Death of Death)."
    assert _strip_card_bullets(source) == source, "source-карточка сохраняет •"


def test_source_map_cards_keep_their_bullet():
    """У «Карты источников» жирный позже снимается целиком — кружок там
    единственный маркер карточки и должен остаться."""
    from converters.md_telegraph import _section_to_nodes_v2

    nodes = _section_to_nodes_v2({
        "title": "Карта источников для дальнейшего изучения",
        "content": "• **Чикагское заявление о безошибочности Писания (1978).**\n\nКлючевой документ.",
    })
    def _txt(n):
        return "".join(
            c if isinstance(c, str) else "".join(map(str, c.get("children", [])))
            for c in n.get("children", [])
        )
    assert any(
        isinstance(n, dict) and n.get("tag") == "p" and _txt(n).lstrip().startswith("•")
        for n in nodes
    )


def test_section_titles_lose_leading_bullets():
    src = (ROOT / "converters/md_telegraph.py").read_text(encoding="utf-8")
    assert r"^\s*[•▪◦‣·]\s*" in src


# ── 5. Навигация честная: fallback + warning вместо ложного успеха ─

def test_navigation_checks_edit_result():
    src = (ROOT / "pipelines/main_pipeline.py").read_text(encoding="utf-8")
    assert "_nav_ok = await _edit_telegraph_page" in src
    assert "у лимита размера" in src
    # компактный fallback без hr
    assert src.count('["📂 "] + nav_children') >= 2, (
        "и главная страница, и части 2+ должны иметь компактный nav-fallback"
    )


# ── 6. Промты: плотность мысли ──────────────────────────────────

def test_study_prompt_has_density_layer():
    from core.prompts import STUDY_ANALYSIS_PROMPT as p
    assert "ПЛОТНОСТЬ МЫСЛИ" in p
    assert "УПУЩЕННЫЕ ВОЗМОЖНОСТИ НАУЧИТЬ" in p
    assert "глубинный ход" in p


def test_reflection_prompt_has_verifiable_application_rule():
    from core.prompts import REFLECTION_APPLICATION_PROMPT as p
    assert "БЕЗ СЛОВ РАДИ СЛОВ" in p
    assert "ПРОВЕРЯЕМЫМ" in p
    assert "Диагностика глубже симптомов" in p
