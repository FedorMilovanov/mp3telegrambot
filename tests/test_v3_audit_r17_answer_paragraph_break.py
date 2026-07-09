"""AUDIT R17 (2026-07-09, самостоятельная проверка дампа Razbor материала
«Свидетельство: Трус и Лжец»): в TYPE 6 («Заблуждения и ответ ортодоксии»)
Gemini иногда не ставит \n\n перед "✅ **Ответ ортодоксальной церкви.**" —
маркер приклеивается одним пробелом к концу предыдущего ❌-абзаца:

    ...маскируя отсутствие реальных плодов Духа. ✅ **Ответ ортодоксальной церкви.**

    Историческое реформатское богословие категорически отвергает...

Промпт (core/prompts.py, TYPE 6) требует отдельный абзац на пару ❌+✅
("Между парами ❌ + ✅ — двойной перенос строки"). Ничто в пайплайне это
не чинило. Добавлена детерминированная вставка \n\n перед ❌/✅, когда
маркер приклеен к предыдущему предложению одним пробелом (не трогаем
случай "**Тема** ❌ **Подмена...**", где ❌ не следует за концом
предложения).

Второй баг того же прогона (дамп Reflection, «Диагностика сердца»):
Gemini приклеил «:» сразу после «?» в конце вопроса-триггера —
"...родительское одобрение?:\n\nЛегко казаться..." — похоже, спутав
формат жирного вопроса с form-меткой «Заголовок:\n\nТекст», которая
используется в других разделах. После «?» двоеточие всегда лишнее.
"""
from converters.md_telegraph import _postprocess_telegraph_nodes, _section_to_nodes_v2

GLUED_CONTENT = (
    "❌ **«Лёгкое верие» (Easy Believism)**\n\n"
    "Заблуждение о том, что для спасения достаточно лишь интеллектуального "
    "согласия с фактами Евангелия, без необходимости покаяния. Это порождает "
    "ложную уверенность у невозрожденных людей, маскируя отсутствие реальных "
    "плодов Духа. ✅ **Ответ ортодоксальной церкви.**\n\n"
    "Историческое реформатское богословие категорически отвергает такое "
    "разделение Христа на Спасителя и Господа."
)


def _walk_collect(node, parts):
    if isinstance(node, str):
        parts.append(node)
    elif isinstance(node, dict):
        for c in node.get("children", []) or []:
            _walk_collect(c, parts)


def _flat_paragraphs(nodes):
    paras = []
    for n in nodes:
        if isinstance(n, dict) and n.get("tag") == "p":
            parts = []
            for c in n.get("children", []) or []:
                _walk_collect(c, parts)
            paras.append("".join(parts))
    return paras


def test_glued_answer_marker_gets_own_paragraph():
    nodes = _section_to_nodes_v2(
        {"title": "Заблуждения и ответ ортодоксии", "time": "", "content": GLUED_CONTENT},
    )
    paras = _flat_paragraphs(nodes)
    assert any(p.rstrip().endswith("Духа.") for p in paras), (
        f"абзац с ❌ должен заканчиваться на «Духа.», без приклеенного ✅: {paras}"
    )
    assert any(p.strip().startswith("✅") for p in paras), (
        f"«✅ Ответ ортодоксальной церкви.» должен начинать собственный абзац: {paras}"
    )
    assert not any("Духа. ✅" in p for p in paras), "склейка не устранена"


def test_theme_cross_replacement_pattern_untouched():
    """Не должны ломать соседний паттерн «**Тема** ❌ **Подмена: ...**» —
    там ❌ не следует за концом предложения, регэксп его не трогает."""
    nodes = _section_to_nodes_v2(
        {"title": "Заблуждения", "time": "", "content": "**Тема** ❌ **Подмена: суть заблуждения.**"},
    )
    paras = _flat_paragraphs(nodes)
    assert len(paras) == 1, f"не должно появиться лишних абзацев: {paras}"


def test_fix_present_in_source():
    from pathlib import Path
    src = Path(__file__).resolve().parents[1].joinpath("converters/md_telegraph.py").read_text(
        encoding="utf-8"
    )
    assert r"(?<=[.!?])[ \t]+(?=[❌✅]\s*\*\*)" in src


# ── «?:» приклеенное двоеточие после вопроса-триггера ────────────

def test_question_mark_colon_glue_stripped():
    """Живой баг из дампа Reflection: приклеенное «?:» не только лишний
    символ — оно ЕЩЁ И глушит существующую эвристику "make standalone
    question paragraphs visually explicit" (plain.endswith('?')), поэтому
    вопрос-триггер TYPE 3 терял жирное выделение, требуемое промптом."""
    text = "Удерживает ли тебя от греха реальный страх перед Богом или просто боязнь потерять репутацию?:"
    out = _postprocess_telegraph_nodes([{"tag": "p", "children": [text]}])
    node = out[0]
    parts = []
    _walk_collect(node, parts)
    fixed = "".join(parts)
    assert fixed.endswith("репутацию?"), f"двоеточие после «?» не убрано: {fixed!r}"
    assert "?:" not in fixed
    assert node["children"][0].get("tag") == "b", (
        "после чистки «?:» вопрос должен снова стать жирным (эвристика "
        f"standalone-question paragraph): {node!r}"
    )


def test_question_mark_colon_glue_not_touched_mid_sentence():
    """Только в конце строки/абзаца — не трогаем гипотетический «?:» внутри
    текста (например, диалоговую разметку), если он не на границе абзаца."""
    text = "Он спросил: «Готов?» — и замолчал."
    out = _postprocess_telegraph_nodes([{"tag": "p", "children": [text]}])
    parts = []
    _walk_collect(out[0], parts)
    assert "".join(parts) == text
