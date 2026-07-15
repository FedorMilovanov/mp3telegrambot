"""Regression tests for R49 — оператор запушил свежие Конспекты/Разборы
(dumps 2026-07-14, проповеди по Исаии 53) и попросил «внимательно изучить по
всем нашим стандартам». Ниже — детерминированные починки найденных дефектов.

Fixes covered:
  #34  Ссылка на Писание в косвенном падеже («Деяний 2:23», «стих 8:32»,
       «Исаии 6:1») превращалась в кликабельный видео-таймкод. Добавлен
       контекст-guard `_SCRIPTURE_CTX_BEFORE`.
  #35  Голый ⏱-таймкод роняли ВНУТРЬ дословной цитаты «…» (span может
       занимать несколько абзацев). `_strip_timestamps_inside_scripture_quotes`.
  #36  Строка Писания, кончавшаяся ITALIC '*', получала точку СНАРУЖИ → '.*.'.
       Новая ветка `elif s.endswith('*')` в `_ensure_trailing_period`.
  #37a «…Джон МакАртур — Джон МакАртур»: имя автора дублировалось, когда видео-
       тайтл уже кончается именем. `join_title_author`.
  #37b Формулировка «Поверхностное чтение упускает…» повторялась дословно из
       проповеди в проповедь — few-shot/инструкции в промпте её якорили.
  #37c Полноширинные CJK-скобки 【…】 из ютуб-тайтла в русском заголовке.
  #37d Ис. 53:5: «мучем за беззакония» → «мучим за беззакония» (Синодальный).
"""

import re
from pathlib import Path

from converters.md_telegraph import (
    _SCRIPTURE_CTX_BEFORE,
    _strip_timestamps_inside_scripture_quotes,
    _ensure_trailing_period,
)
from core.text_utils import (
    join_title_author,
    normalize_title_text,
    normalize_common_typos,
)


# ── #34. Scripture-ref в косвенном падеже не является видео-таймкодом ─────────
def test_r49_scripture_context_guard_matches_declensions():
    # «before» — текст ДО числа N:NN; guard должен сработать (это не таймкод).
    for before in (
        "В книге Деяний ",
        "Деяний ",
        "как сказано в Исаии ",
        "Исаия ",
        "стих ",
        "он читает стих ",          # «стих 8:32» — число идёт сразу за словом
        "стих пророка ",            # промежуточное слово опционально
        "в главе ",
        "Псалом ",
    ):
        assert _SCRIPTURE_CTX_BEFORE.search(before), f"guard missed: {before!r}"


def test_r49_scripture_context_guard_does_not_match_plain_prose():
    # Обычный текст перед таймкодом — guard молчит, линкификация разрешена.
    for before in (
        "И тогда он сказал в ",
        "перемотай на ",
        "мы начинаем с ",
    ):
        assert not _SCRIPTURE_CTX_BEFORE.search(before), f"false positive: {before!r}"


# ── #35. Голый ⏱ внутри «…»-цитаты вырезается (в т.ч. multi-paragraph) ───────
def test_r49_strip_timestamp_inside_multiparagraph_quote():
    content = (
        "*«Он был презираем, и мы ни во что ставили Его ⏱ 2:18. Но Он взял на "
        "Себя наши немощи.*\n\n"
        "*Но Он изъязвлен был за грехи наши ⏱ 2:51. Он истязуем был.*\n\n"
        "*за преступников сделался ходатаем».*"
    )
    out = _strip_timestamps_inside_scripture_quotes(content)
    for ts in ("⏱ 2:18", "⏱ 2:51"):
        assert ts not in out, f"{ts} survived inside quote"
    assert "  " not in out            # без двойных пробелов
    assert " ." not in out.replace(" .*", ".*")  # без осиротевшей « .»


def test_r49_timestamp_outside_quote_is_preserved():
    outside = "Этот текст — неисчерпаемый источник ⏱ 0:45. Чем больше исследую."
    assert _strip_timestamps_inside_scripture_quotes(outside) == outside


def test_r49_timestamp_after_closed_quote_is_preserved():
    after = "*«Кто поверил слышанному».* И далее мысль ⏱ 5:21. Продолжение."
    assert _strip_timestamps_inside_scripture_quotes(after) == after


def test_r49_unbalanced_quotes_skipped_for_safety():
    unbal = "«открыли но не закрыли ⏱ 1:00 дальше текст без закрытия"
    assert _strip_timestamps_inside_scripture_quotes(unbal) == unbal


def test_r49_strip_wired_into_section_pipeline():
    src = Path("converters/md_telegraph.py").read_text(encoding="utf-8")
    assert "_strip_timestamps_inside_scripture_quotes(content)" in src


# ── #36. ITALIC-строка Писания не получает '.*.' ─────────────────────────────
def test_r49_italic_line_already_ended_with_period_unchanged():
    line = "*…наказуем и уничижен Богом.*"
    assert _ensure_trailing_period(line) == line


def test_r49_italic_line_without_period_gets_period_inside_italic():
    line = "*…наказуем и уничижен Богом*"
    out = _ensure_trailing_period(line)
    assert out == "*…наказуем и уничижен Богом.*"
    assert ".*." not in out


# ── #37a. Дубликат автора в заголовке ────────────────────────────────────────
def test_r49_join_title_author_no_duplicate_when_title_ends_with_author():
    title = "Евангелие от Бога - 1 - Приводящий в Изумление Раб Иеговы (Исаия 53) Джон МакАртур"
    assert join_title_author(title, "Джон МакАртур") == title


def test_r49_join_title_author_appends_when_absent():
    assert join_title_author("Поразительный Раб Иеговы", "Джон МакАртур") == \
        "Поразительный Раб Иеговы — Джон МакАртур"


def test_r49_join_title_author_ignores_placeholder_author():
    assert join_title_author("Проповедь", "Автор не указан") == "Проповедь"


def test_r49_join_title_author_partial_word_is_not_a_match():
    # «Артур» ≠ «МакАртур»: частичное совпадение хвоста не глушит суффикс.
    assert join_title_author("Проповедь Артура", "Джон МакАртур") == \
        "Проповедь Артура — Джон МакАртур"


def test_r49_join_title_author_used_in_related_and_page_builders():
    md = Path("converters/md_telegraph.py").read_text(encoding="utf-8")
    assert "join_title_author(title, author)" in md
    tp = Path("services/telegraph_pages.py").read_text(encoding="utf-8")
    assert "join_title_author(prompt_title, real_author)" in tp
    # старый наивный конкат больше не используется в построении tg_title
    assert 'f"{tg_title} — {real_author}"' not in tp
    assert 'f"{prompt_title} — {real_author}"' not in tp
    # Synopsis/Конспект (базовая страница) собирает заголовок в telegraph.py —
    # именно здесь возникал дубль в дампе Evangelie-ot-Boga; он тоже покрыт.
    tg = Path("services/telegraph.py").read_text(encoding="utf-8")
    assert "join_title_author(tg_title, author)" in tg
    assert 'f"{tg_title} — {author}"' not in tg


# ── #37b. Якорь «поверхностное чтение упускает» снят из промпта ──────────────
def test_r49_prompt_no_longer_anchors_superficial_reading_stem():
    src = Path("core/prompts.py").read_text(encoding="utf-8")
    # дословный few-shot-якорь и инструкция-заголовок с тем же штампом убраны
    assert "Что поверхностное чтение упускает" not in src
    assert "что упускает поверхностное чтение" not in src
    assert "что часто понимают поверхностно" not in src


def test_r49_prompt_bans_semantic_node_cliche():
    src = Path("core/prompts.py").read_text(encoding="utf-8")
    assert "семантический узел активирован" in src


# ── #37c. Полноширинные скобки 【…】 в заголовке нормализуются ────────────────
def test_r49_fullwidth_brackets_normalized_in_title():
    t = "Приводящий в Изумление Раб Иеговы【исаия 53】Джон МакАртур"
    out = normalize_title_text(t)
    assert "【" not in out and "】" not in out
    assert "(" in out and ")" in out


# ── #37d. Ис. 53:5 «мучем за» → «мучим за» (Синодальный) ─────────────────────
def test_r49_isaiah_53_5_mucem_typo_fixed():
    assert normalize_common_typos(
        "Он был изъязвлен за грехи наши и мучем за беззакония наши",
        source_map=False,
    ) == "Он был изъязвлен за грехи наши и мучим за беззакония наши"


def test_r49_mucem_correction_is_anchored_not_context_blind():
    # правка привязана к «мучем за» — иные контексты не задеваем (их и нет,
    # но фиксируем намерение: голое «мучем» без «за» не трогаем этой парой)
    src = Path("core/text_utils.py").read_text(encoding="utf-8")
    assert '("мучем за", "мучим за")' in src
