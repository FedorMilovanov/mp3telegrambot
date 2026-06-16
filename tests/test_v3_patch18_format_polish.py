"""Tests for v3 patch 18 — Telegraph formatting polish.

Covers formatting bugs seen in live Study pages:
- scripture header+quote must be separated from explanation paragraph;
- quote-first scripture bullets must be normalized to bold ref + italic quote;
- duplicate spaces around bold/italic inline nodes must be removed;
- prompts must instruct the model to use the same structure.
"""

from converters.md_telegraph import _postprocess_telegraph_nodes


def _p(children):
    return {"tag": "p", "children": children}


def _flat(node):
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "".join(_flat(c) for c in node.get("children", []))
    return ""


def test_scripture_quote_and_explanation_are_split():
    nodes = [
        _p([
            "• ",
            {"tag": "b", "children": ["1 Коринфянам 1:26 – 28: "]},
            " ",
            {"tag": "i", "children": ["«Посмотрите, братия...»"]},
            " Разбор социального состава коринфской общины служит доказательством Божьего метода.",
        ])
    ]
    out = _postprocess_telegraph_nodes(nodes)
    paragraphs = [n for n in out if isinstance(n, dict) and n.get("tag") == "p"]
    assert len(paragraphs) == 2
    assert _flat(paragraphs[0]) == "• 1 Коринфянам 1:26–28: «Посмотрите, братия...»"
    assert _flat(paragraphs[1]).startswith("Разбор социального состава")


def test_quote_first_bullet_becomes_bold_ref_micro_block():
    nodes = [_p(["• ", "«Как прах, всеми попираемый» ", {"tag": "i", "children": [" (1 Кор 4:13)"]}, "."])]
    out = _postprocess_telegraph_nodes(nodes)
    p = out[0]
    assert p["children"][1]["tag"] == "b"
    assert p["children"][1]["children"] == ["1 Кор 4:13:"]
    assert p["children"][3]["tag"] == "i"
    assert p["children"][3]["children"] == ["«Как прах, всеми попираемый»"]
    assert _flat(p) == "• 1 Кор 4:13: «Как прах, всеми попираемый»"


def test_bolded_quote_first_bullet_is_unwrapped_to_ref_first():
    nodes = [_p(["• ", {"tag": "b", "children": ["«Безумное мира» ", {"tag": "i", "children": ["(1 Кор 1:27)"]}, "."]}])]
    out = _postprocess_telegraph_nodes(nodes)
    p = out[0]
    assert p["children"][1]["tag"] == "b"
    assert p["children"][1]["children"] == ["1 Кор 1:27:"]
    assert p["children"][3]["children"] == ["«Безумное мира»"]


def test_duplicate_space_after_bold_is_removed():
    nodes = [_p(["• ", {"tag": "b", "children": ["Суверенная благодать в эффективном призвании. "]}, " Согласно 1689."])]
    out = _postprocess_telegraph_nodes(nodes)
    assert _flat(out[0]) == "• Суверенная благодать в эффективном призвании. Согласно 1689."
    assert out[0]["children"][1]["children"] == ["Суверенная благодать в эффективном призвании."]


def test_study_prompt_requires_explanation_below_scripture_quote():
    from core.prompts import STUDY_ANALYSIS_PROMPT
    assert "разбор — отдельным абзацем ниже" in STUDY_ANALYSIS_PROMPT
    assert "НЕ сливай цитату и объяснение в одну строку" in STUDY_ANALYSIS_PROMPT


def test_prompt_forbids_inline_explanation_after_quote():
    from core.prompts import STUDY_ANALYSIS_PROMPT
    assert "объяснение сразу после цитаты в той же строке" in STUDY_ANALYSIS_PROMPT


def test_audio_prompt_main_topic_bans_pompous_author_labels():
    src = open("core/prompts.py", encoding="utf-8").read()
    assert "ПРАВИЛО УПОМИНАНИЯ АВТОРОВ В main_topic" in src
    assert "богословские гиганты" in src
    assert "имя/роль автора + анализирует/показывает" in src
    assert "Панель обсуждает" in src


def test_audio_prompt_json_example_no_third_person_main_topic():
    from core.prompts import build_audio_analysis_prompt
    prompt = build_audio_analysis_prompt("T", "", "60:00", 3600, mode="deep")
    example_pos = prompt.find('"main_topic"')
    example = prompt[example_pos:example_pos + 260]
    assert "МакАртур задаёт" not in example
    assert "Он вскрывает" not in example
    assert "Что если" in example or "Материал вскрывает" in example
