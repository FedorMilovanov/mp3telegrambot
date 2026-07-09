"""AUDIT R18 (2026-07-09, повторный прогон «Что такое Евангелие? — Р.С. Спраул»,
Reflection dump, раздел «Духовные подмены и библейские контрасты»):

    Желание понравиться неверующим друзьям и сделать весть комфортной
    ⏱ 41:14. ⏱ 41:14

    Шаг: В следующем разговоре о вере расскажите не о том...

Тот же класс бага уже наблюдался дважды раньше в этой же сессии (в разделах
«Практика» двух других видео, оба тоже без жирного триггера перед «Шаг:»).
Корень: контракт "application"-блока (core/prompts.py: challenge +
anchor_timestamp + concrete_step) ВСЕГДА даёт жирный триггер, когда Gemini
кладёт данные в structured "blocks" (_structured_blocks_to_nodes_v2:
`line = f"**{challenge}**"`). Но когда Gemini вместо этого пишет тот же
паттерн обычным текстом в плоском "content" — жирности никто не
гарантирует. Заодно тот же баг задвоил один и тот же inline-таймкод.

Фикс: в _postprocess_telegraph_nodes, если абзац начинается с "Шаг:" и
предыдущий абзац ещё не жирный — оборачиваем предыдущий абзац в <b>
(дожимая контракт постфактум) и убираем задвоенный inline-таймкод.
"""
from converters.md_telegraph import _postprocess_telegraph_nodes


def _walk(node, parts):
    if isinstance(node, str):
        parts.append(node)
    elif isinstance(node, dict):
        for c in node.get("children", []) or []:
            _walk(c, parts)


def _flat(node) -> str:
    parts = []
    _walk(node, parts)
    return "".join(parts)


TS_LINK = {
    "tag": "a",
    "attrs": {"href": "https://www.youtube.com/watch?v=39h_FY_37Vk&t=2474"},
    "children": ["⏱ 41:14"],
}


def test_non_bold_trigger_before_shag_gets_wrapped_bold():
    nodes = [
        {"tag": "p", "children": [
            "Желание понравиться неверующим друзьям и сделать весть комфортной ",
            dict(TS_LINK),
            ". ",
            dict(TS_LINK),
        ]},
        {"tag": "p", "children": [
            "Шаг: В следующем разговоре о вере расскажите не о том, как вам стало хорошо в церкви."
        ]},
    ]
    out = _postprocess_telegraph_nodes(nodes)
    trigger_para = out[0]
    assert trigger_para["children"][0].get("tag") == "b", (
        f"триггер перед «Шаг:» должен стать жирным (контракт application-блока): {trigger_para!r}"
    )
    assert "Желание понравиться" in _flat(trigger_para)


def test_duplicate_adjacent_timestamp_link_collapsed():
    nodes = [
        {"tag": "p", "children": [
            "Желание понравиться неверующим друзьям и сделать весть комфортной ",
            dict(TS_LINK),
            ". ",
            dict(TS_LINK),
        ]},
        {"tag": "p", "children": ["Шаг: сделать нечто конкретное."]},
    ]
    out = _postprocess_telegraph_nodes(nodes)
    bold_node = out[0]["children"][0]
    links = [c for c in bold_node["children"] if isinstance(c, dict) and c.get("tag") == "a"]
    assert len(links) == 1, f"дублирующийся таймкод не убран: {bold_node!r}"


def test_already_bold_trigger_left_untouched_no_double_wrap():
    nodes = [
        {"tag": "p", "children": [{"tag": "b", "children": ["Уже жирный триггер ⏱ 10:00."]}]},
        {"tag": "p", "children": ["Шаг: сделать нечто конкретное."]},
    ]
    out = _postprocess_telegraph_nodes(nodes)
    trigger_para = out[0]
    # ровно один уровень <b>, не <b><b>...
    assert trigger_para["children"][0].get("tag") == "b"
    inner = trigger_para["children"][0]["children"]
    assert not any(isinstance(c, dict) and c.get("tag") == "b" for c in inner), (
        f"триггер задвоил жирный тег: {trigger_para!r}"
    )


def test_paragraph_not_followed_by_shag_is_untouched():
    """Обычные соседние абзацы (без «Шаг:» после) не должны неожиданно жирнеть."""
    nodes = [
        {"tag": "p", "children": ["Обычный абзац без применения."]},
        {"tag": "p", "children": ["Другой обычный абзац."]},
    ]
    out = _postprocess_telegraph_nodes(nodes)
    assert out[0]["children"] == ["Обычный абзац без применения."]


def test_fix_present_in_source():
    from pathlib import Path
    src = Path(__file__).resolve().parents[1].joinpath("converters/md_telegraph.py").read_text(
        encoding="utf-8"
    )
    assert "_dedup_adjacent_timestamp_link" in src
    assert "startswith('Шаг:')" in src
