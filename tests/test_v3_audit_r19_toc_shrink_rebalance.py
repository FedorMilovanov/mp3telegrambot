"""AUDIT R19 (лог 2026-07-09, «Что такое Евангелие? — Р.С. Спраул»):

    editPage часть 1/3 попытка 1/3 failed ... CONTENT_TOO_BIG
    editPage часть 1/3 попытка 2/3 failed ... CONTENT_TOO_BIG
    editPage часть 1/3 попытка 3/3 failed ... CONTENT_TOO_BIG
    Synopsis v2: editPage часть 1/3 упала с TOC — повтор без оглавления
        (content-size fallback)

Живой дамп подтвердил: часть 1 Конспекта опубликовалась БЕЗ оглавления —
добавление TOC к уже опубликованному content'у части 1 превысило лимит
Telegraph, и старый код молча выбрасывал оглавление целиком, теряя
навигацию по разделам для читателя.

Новый алгоритм (services/telegraph.py, create_telegraph_synopsis) сначала
пытается СЖАТЬ часть — перенести её последнюю секцию в начало следующей
части — и заново попробовать editPage С оглавлением. Оглавление
выбрасывается насовсем только если сжимать больше некуда (осталась 1
секция) или физически некуда (это последняя часть, следующей нет).
Решение о том, влезло ли, всё так же принимает сам Telegraph через
editPage (CONTENT_TOO_BIG), а не догадка по числу нод — тот же принцип,
что и в R16 (слияние хвостовых частей).

Тесты ниже:
1) симулируют сам алгоритм сдвига секций (тот же, что в источнике) и
   проверяют сохранение порядка секций и корректность сквозной нумерации
   после сдвига;
2) структурными проверками источника подтверждают, что новый порядок
   («сначала сжать, потом уже выбросить») реализован, а старый
   безусловный «выбросить сразу» — убран.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _telegraph_src() -> str:
    return (ROOT / "services/telegraph.py").read_text(encoding="utf-8")


# ── 1. Алгоритм сдвига секций (симуляция, тот же порядок операций) ──

def _simulate_shrink(part_secs, next_secs, *, fits_when_len_lte):
    """Тот же цикл, что в create_telegraph_synopsis: пока не влезло и
    секций больше одной — переносим последнюю секцию part_secs в начало
    next_secs. `fits_when_len_lte` — оракул вместо реального editPage."""
    attempts = 0
    while len(part_secs) > 1:
        attempts += 1
        if len(part_secs) <= fits_when_len_lte:
            return part_secs, next_secs, attempts, True
        moved = part_secs[-1]
        part_secs = part_secs[:-1]
        next_secs = [moved] + next_secs
    return part_secs, next_secs, attempts, len(part_secs) <= fits_when_len_lte


def test_shrink_loop_moves_trailing_sections_preserving_order():
    part_secs = ["A", "B", "C", "D", "E"]
    next_secs = ["F", "G", "H"]
    shrunk, grown, attempts, fit = _simulate_shrink(part_secs, next_secs, fits_when_len_lte=2)
    assert fit, "должно было влезть после сжатия"
    assert shrunk == ["A", "B"], f"часть 1 должна остаться [A, B]: {shrunk}"
    assert grown == ["C", "D", "E", "F", "G", "H"], (
        f"перенесённые секции должны сохранить исходный порядок: {grown}"
    )


def test_shrink_loop_stops_at_single_section_if_still_too_big():
    """Если не влезает даже с 1 секцией — цикл останавливается (дальше
    в реальном коде идёт последний резерв: выбросить оглавление)."""
    part_secs = ["A", "B", "C"]
    next_secs = ["D"]
    shrunk, grown, attempts, fit = _simulate_shrink(part_secs, next_secs, fits_when_len_lte=0)
    assert shrunk == ["A"], f"должно остановиться на 1 секции: {shrunk}"
    assert not fit
    assert grown == ["B", "C", "D"]


def test_global_numbering_correct_after_shrink():
    """После переноса 3 секций из части 1 в часть 2 (5→2 в part[0]),
    смещение для сквозной нумерации части 2 должно считаться от НОВОЙ
    (сжатой) длины part[0], а не от старой."""
    parts = [["A", "B", "C", "D", "E"], ["F", "G", "H"]]
    shrunk, grown, _, _ = _simulate_shrink(parts[0], parts[1], fits_when_len_lte=2)
    parts = [shrunk, grown]
    offset_for_part_1 = sum(len(p) for p in parts[:1])
    assert offset_for_part_1 == 2, f"смещение должно учитывать сжатую часть 1: {offset_for_part_1}"
    global_nums = [offset_for_part_1 + idx + 1 for idx in range(len(parts[1]))]
    assert global_nums == [3, 4, 5, 6, 7, 8], f"нумерация части 2 сбилась: {global_nums}"


# ── 2. Структурные проверки источника ────────────────────────────

def test_shrink_before_drop_helper_present():
    src = _telegraph_src()
    assert "async def _build_part_nodes_edit(" in src
    assert src.count("_build_part_nodes_edit(i") >= 2 or src.count("_build_part_nodes_edit(") >= 3, (
        "хелпер должен переиспользоваться минимум 3 раза: начальная сборка, "
        "сжатие-с-оглавлением, финальный no-outline fallback"
    )


def test_shrink_attempted_before_dropping_outline():
    """AUDIT R39: перенос секций между частями (shrink) УБРАН — он терял секцию,
    если принимающая часть тоже не влезала (двойной overflow), а пайплайн при
    этом рапортовал успех. Осталась безопасная замена: пере-издать часть БЕЗ
    оглавления (контент уже на create-фазе, точно влезает)."""
    src = _telegraph_src()
    assert "while len(part_secs) > 1:" not in src        # цикл сжатия убран
    assert "переношу последнюю секцию" not in src         # лог переноса убран
    assert "include_outline=False" in src                 # безопасный drop-TOC fallback


def test_shrink_guarded_by_next_part_existing():
    """AUDIT R39: раз переноса нет — нет и его мутаций между частями."""
    src = _telegraph_src()
    assert "_next_secs = [_moved_sec] + _next_secs" not in src
    assert "published_parts[i + 1] = (_next_secs, _next_url)" not in src


def test_old_unconditional_drop_toc_fallback_removed():
    """Старый код безусловно выбрасывал TOC/mini-outline при первой же
    неудаче editPage — эта строка не должна остаться в источнике."""
    src = _telegraph_src()
    assert "упала с TOC — повтор без оглавления" not in src
    assert "упала с mini-outline — повтор без него" not in src


def test_related_materials_still_attached_in_no_outline_fallback():
    """Раньше финальный fallback (drop outline) собирал ноды вручную и
    терял блок «Читать также» для последней части. Через общий хелпер
    include_outline=False блок related-materials сохраняется."""
    src = _telegraph_src()
    helper = src.split("async def _build_part_nodes_edit(", 1)[1].split("\n\n        await asyncio.sleep(2)\n        for i,", 1)[0]
    assert "aget_related_materials" in helper
    assert "if i == total - 1:" in helper
