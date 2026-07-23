from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATH = ROOT / "services" / "study_synthesis_runtime.py"


def _load_runtime():
    spec = importlib.util.spec_from_file_location("study_synthesis_runtime_test", RUNTIME_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _format_prompt(prompt: str) -> str:
    return prompt.format(
        title="Вопросы и ответы",
        author="Автор",
        duration="45:00",
        format_name="discussion",
        hermeneutic_method="mixed",
        main_topic="Богословская точность",
        analysis_summary="Краткий анализ",
        argument_arc="Ход мысли",
        key_categories="любовь; христология",
        timestamps="9:31; 13:04",
        concepts="любовь благоволения",
        scripture="Ин. 3:36",
        translations="не указаны",
        lexicon_notes="не указаны",
        synopsis_context="Дословная стенограмма",
        source_pack="Разрешённые источники",
    )


def test_teacherly_prompt_is_material_led_prose_not_rubric() -> None:
    runtime = _load_runtime()
    rendered = _format_prompt(runtime.TEACHERLY_STUDY_PROMPT)

    assert "хорошую богословскую главу" in rendered
    assert "КОМПОЗИЦИЯ: УПРАВЛЯЕМАЯ СВОБОДА" in rendered
    assert "Не используй blocks по умолчанию" in rendered
    assert "Не создавай цепочки карточек" in rendered
    assert "не описывай ролик со стороны" in rendered
    assert "до 1000 слов" not in rendered
    assert "SECTION TYPE 1" not in rendered
    assert "5–10 карточек" not in rendered
    assert "Русская фраза стиха:" not in rendered
    assert "Базовое значение:" not in rendered


def test_word_study_renders_as_one_natural_paragraph() -> None:
    runtime = _load_runtime()
    block = runtime.render_word_study_as_prose(
        {
            "type": "word_study",
            "scripture_ref": "2 Тим. 3:16",
            "russian_quote": "Всё Писание богодухновенно",
            "russian_focus": "богодухновенно",
            "original_form": "θεόπνευστος",
            "lemma": "θεόπνευστος",
            "transliteration": "theopneustos",
            "russian_pronunciation": "теопневстос",
            "grammar": "именная форма единственного числа",
            "basic_meaning": "данное Богом, вдохновлённое Богом",
            "meaning_in_context": "качество Писания связывается с его происхождением от Бога",
            "role_in_argument": "Поэтому Писание формирует богословие и проверяет церковную традицию",
            "limits_of_claim": "Само слово не объясняет весь способ вдохновения Писания",
            "source": "проверенный греческий текст и словарь",
            "anchor_timestamp": "3:15",
        }
    )

    assert block is not None
    text = block["text"]
    assert block["type"] == "paragraph"
    assert "\n\n" not in text
    assert "**2 Тим. 3:16 — «богодухновенно».**" in text
    assert "θεόπνευστος" in text
    assert "⏱ **3:15**" in text
    assert "Базовое значение:" not in text
    assert "В этом стихе:" not in text
    assert "Роль в аргументе" not in text
    assert "Граница вывода:" not in text
    assert "Источник:" not in text


def test_incomplete_decorative_word_is_dropped() -> None:
    runtime = _load_runtime()
    assert runtime.render_word_study_as_prose(
        {
            "type": "word_study",
            "lemma": "spoudazō",
            "role_in_argument": "Это подчёркивает усердие",
        }
    ) is None


def test_one_telegraph_page_budget_replaces_short_word_cap() -> None:
    profile_source = (ROOT / "core" / "analysis_profiles.py").read_text(encoding="utf-8")
    assert "бюджета одной насыщенной Telegraph-страницы" in profile_source
    assert "не обрезай глубокий разбор из-за условного лимита слов" in profile_source
    assert 'target_chars="8000–16000 символов"' in profile_source
    assert 'target_chars="12000–22000 символов"' in profile_source
    assert 'target_chars="16000–26000 символов"' in profile_source


def test_teacherly_runtime_is_final_prompt_layer() -> None:
    init_source = (ROOT / "services" / "__init__.py").read_text(encoding="utf-8")
    quality_pos = init_source.index("install_conspect_quality_contract()")
    audit_pos = init_source.index("install_conspect_audit_runtime()")
    teacher_pos = init_source.index("install_teacherly_study_runtime()")
    assert quality_pos < audit_pos < teacher_pos


def test_agent_contract_prevents_rubric_regression() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "Teacherly Study prose rule" in agents
    assert "coherent teaching chapter" in agents
    assert "not the visible answer to an internal checklist" in agents
    assert "Let the material choose its architecture" in agents
    assert "services.study_synthesis_runtime" in agents
