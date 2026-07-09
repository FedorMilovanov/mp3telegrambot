"""AUDIT R14 (2026-07-09): частичный листинг RuTube отравлял кэш.

Живой лог: видео 1 (Константин) рано вышло на стр.58 (1160 видео) — этот
ЧАСТИЧНЫЙ листинг закэшировался; видео 2 (Трус и лжец) взяло его из кэша и
не нашло свой ролик («не найдено в листинге»), хотя он есть на канале
(VK поймал 0.90). Причина — кэширование листинга, обрезанного ранним выходом.
"""
import time
from pathlib import Path

import services.search as S

ROOT = Path(__file__).resolve().parents[1]


def _reset():
    S._RUTUBE_LISTING_CACHE.clear()


def test_cache_stores_completeness_flag():
    _reset()
    S._set_rutube_listing_cache("c1", [{"id": "1"}], False)
    res, complete = S._get_rutube_listing_cache("c1")
    assert res == [{"id": "1"}]
    assert complete is False


def test_partial_cache_does_not_overwrite_complete():
    """Полный листинг не должен затираться частичным (другое видео рано вышло)."""
    _reset()
    S._set_rutube_listing_cache("c1", [{"id": a} for a in "12345"], True)
    S._set_rutube_listing_cache("c1", [{"id": "1"}], False)  # частичный — должен игнорироваться
    res, complete = S._get_rutube_listing_cache("c1")
    assert complete is True
    assert len(res) == 5


def test_complete_cache_can_be_set_over_partial():
    """Частичный кэш ЗАМЕНЯЕТСЯ полным при дозагрузке."""
    _reset()
    S._set_rutube_listing_cache("c1", [{"id": "1"}], False)
    S._set_rutube_listing_cache("c1", [{"id": a} for a in "123456"], True)
    res, complete = S._get_rutube_listing_cache("c1")
    assert complete is True
    assert len(res) == 6


def test_cache_ttl_expiry_still_works():
    _reset()
    S._set_rutube_listing_cache("c1", [{"id": "1"}], True)
    # искусственно состарим запись
    ts, results, complete = S._RUTUBE_LISTING_CACHE["c1"]
    S._RUTUBE_LISTING_CACHE["c1"] = (ts - S._RUTUBE_LISTING_CACHE_TTL - 1, results, complete)
    assert S._get_rutube_listing_cache("c1") is None


def test_search_rutube_resumes_on_partial_cache_miss():
    """Ключевой сценарий бага: частичный кэш без совпадения → дозагрузка."""
    src = (ROOT / "services/search.py").read_text(encoding="utf-8")
    fn = src.split("async def search_rutube", 1)[1]
    assert "not listing_complete" in fn
    assert "дозагружаю остаток" in fn
    assert "allow_early_exit=False" in fn


def test_load_helper_marks_early_exit_incomplete():
    """_load_rutube_listing помечает ранний выход как complete=False."""
    src = (ROOT / "services/search.py").read_text(encoding="utf-8")
    helper = src.split("async def _load_rutube_listing", 1)[1].split("async def search_rutube", 1)[0]
    assert "complete = False" in helper
    assert "досрочный выход" in helper
    # ранний выход управляем флагом allow_early_exit
    assert "allow_early_exit and" in helper


# ── Тайминг-фикс из того же прогона: точка после ⏱ сквозь жирный ──

def test_ts_period_order_handles_bold_close_before_timestamp():
    """Дамп 07-09: application-блок Reflection даёт «**…истории.** ⏱ 20:17»
    (точка ВНУТРИ жирного). Точка обязана уехать за таймкод, жирный — цел."""
    from converters.md_telegraph import _fix_ts_period_order
    got = _fix_ts_period_order("**Лень и привычка доверять пересказам вместо исследования истории.** ⏱ 20:17")
    assert got == "**Лень и привычка доверять пересказам вместо исследования истории** ⏱ 20:17."
    # plain-случаи не сломаны
    assert _fix_ts_period_order("к свободе. ⏱ 12:15.") == "к свободе ⏱ 12:15."
    assert _fix_ts_period_order("крушение рамок. ⏱ 6:16") == "крушение рамок ⏱ 6:16."


def test_application_block_no_period_before_timestamp():
    """Сквозной путь application-блока: в готовых узлах нет «. ⏱»."""
    from converters.md_telegraph import _structured_blocks_to_nodes_v2
    nodes = _structured_blocks_to_nodes_v2([
        {"type": "application",
         "challenge": "Интеллектуальная лень и доверие пересказам вместо личного исследования истории.",
         "anchor_timestamp": "20:17", "concrete_step": "Прочитать Послание."},
    ], yt_url="https://youtu.be/x", duration=3600)
    flat = []
    def walk(n):
        if isinstance(n, str):
            flat.append(n)
        elif isinstance(n, dict):
            for c in n.get("children", []) or []:
                walk(c)
    for n in nodes:
        walk(n)
    text = "".join(flat)
    assert "⏱" in text
    assert ". ⏱" not in text and ".⏱" not in text, f"точка перед таймкодом: {text!r}"
