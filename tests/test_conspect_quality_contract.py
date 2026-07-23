from __future__ import annotations

from pathlib import Path


def test_operator_contract_is_recorded_for_future_agents() -> None:
    agents = Path("AGENTS.md").read_text(encoding="utf-8")
    assert "Synopsis fidelity and multipart rule" in agents
    assert "Never introduce an arbitrary maximum-parts cap" in agents
    assert "Study orthodoxy pair-card rule" in agents
    assert "Заблуждения и ответ ортодоксии" in agents
    assert "Ответ ортодоксальной церкви." in agents
    assert "Original-language study is verse-first" in agents


def test_install_preserves_synopsis_and_hardens_only_study() -> None:
    from core import prompts
    from services.conspect_quality_contract import install_conspect_quality_contract

    synopsis_before = prompts.SYNOPSIS_PROMPT_V2
    qa_before = prompts.SYNOPSIS_PROMPT_QA

    status = install_conspect_quality_contract()

    assert prompts.SYNOPSIS_PROMPT_V2 == synopsis_before
    assert prompts.SYNOPSIS_PROMPT_QA == qa_before
    assert "OPERATOR CONSPECT CONTRACT 2026-07-23" in prompts.STUDY_ANALYSIS_PROMPT
    assert "Ключевые слова в контексте Писания" in prompts.STUDY_ANALYSIS_PROMPT
    assert "2–5 содержательных карточек" in prompts.STUDY_ANALYSIS_PROMPT
    assert "0–3 блока; отсутствие блока является нормальным результатом" in prompts.STUDY_ANALYSIS_PROMPT
    assert "conspect contract" in status or "verbatim Synopsis" in status


def test_hardened_study_prompt_is_idempotent_and_keeps_pair_cards() -> None:
    from services.conspect_quality_contract import build_hardened_study_prompt

    original = """5–10 карточек. Каждая карточка — отдельный микро-блок, НЕ сливать в поток.
SECTION TYPE 3 — ЯЗЫКИ ОРИГИНАЛА И ЛЕКСИКО-СЕМАНТИЧЕСКИЕ УЗЛЫ
- Не больше 3–8 слов
- Только действительно важные слова
"""
    once = build_hardened_study_prompt(original)
    twice = build_hardened_study_prompt(once)

    assert once == twice
    assert "5–10 карточек" not in once
    assert "3–8 слов" not in once
    assert "Заблуждения и ответ ортодоксии" in once
    assert "❌ **Подмена: название заблуждения.**" in once
    assert "✅ **Ответ ортодоксальной церкви.**" in once
    assert "ReflectionApplication" in once


def test_complete_word_study_becomes_contextual_russian_block() -> None:
    from services.conspect_quality_contract import normalize_word_study_block

    block = normalize_word_study_block(
        {
            "type": "word_study",
            "scripture_ref": "Ефесянам 4:3",
            "russian_quote": "стараясь сохранять единство Духа в союзе мира",
            "russian_focus": "стараясь",
            "original_form": "σπουδάζοντες",
            "lemma": "σπουδάζω",
            "transliteration": "spoudazō",
            "russian_pronunciation": "спуда́зо",
            "grammar": "причастие настоящего времени",
            "basic_meaning": "проявлять усердие и прилагать серьёзное старание",
            "meaning_in_context": "единство дано Духом, но верующие обязаны деятельно его хранить",
            "role_in_argument": "исключает пассивное ожидание мира в общине",
            "limits_of_claim": "слово не учит, что человек создаёт единство собственными силами",
            "source": "NA28; BDAG",
            "anchor_timestamp": "40:18",
        }
    )

    assert block is not None
    assert block["type"] == "paragraph"
    text = block["text"]
    assert "Ефесянам 4:3" in text
    assert "Русская фраза стиха" in text
    assert "σπουδάζοντες" in text
    assert "σπουδάζω" in text
    assert "спуда́зо" in text
    assert "Базовое значение" in text
    assert "В этом стихе" in text
    assert "Граница вывода" in text
    assert "NA28; BDAG" in text
    assert "⏱ 40:18." in text


def test_thin_legacy_lexicon_card_is_dropped() -> None:
    from services.conspect_quality_contract import normalize_word_study_block

    assert normalize_word_study_block(
        {
            "type": "lexicon",
            "lemma": "spoudazō (σπουδάζω, греч.)",
            "role_in_argument": "Исключает пассивность.",
        }
    ) is None


def test_expanded_schema_exposes_full_word_study_contract() -> None:
    from core import candidate_schema
    from services.conspect_quality_contract import install_conspect_quality_contract

    install_conspect_quality_contract()
    schema = candidate_schema.expanded_page_response_schema()
    block = schema["properties"]["sections"]["items"]["properties"]["blocks"]["items"]

    assert "word_study" in block["properties"]["type"]["enum"]
    for field in (
        "scripture_ref",
        "russian_quote",
        "russian_focus",
        "original_form",
        "lemma",
        "transliteration",
        "russian_pronunciation",
        "basic_meaning",
        "meaning_in_context",
        "role_in_argument",
        "limits_of_claim",
        "source",
        "anchor_timestamp",
    ):
        assert field in block["properties"]


def test_audit_runtime_removes_thin_legacy_card_instead_of_resurrecting_it() -> None:
    from core import content_audit
    from services.conspect_audit_runtime import install_conspect_audit_runtime

    install_conspect_audit_runtime()
    sections, _outline, issues = content_audit.audit_expanded_sections(
        [
            {
                "title": "Ключевые слова в контексте Писания",
                "time": "0:00",
                "content": "",
                "blocks": [
                    {
                        "type": "lexicon",
                        "lemma": "spoudazō (σπουδάζω, греч.)",
                        "role_in_argument": "Исключает пассивность.",
                    }
                ],
            }
        ],
        [{"title": "Ключевые слова в контексте Писания", "time": "0:00"}],
        label="StudyAnalysis",
    )

    assert sections[0]["blocks"] == []
    assert any(issue.code == "word_study_dropped" for issue in issues)
    assert not any(issue.code == "lexicon_role_thin_warning" for issue in issues)


def test_live_typo_repair_is_anchored_and_deterministic() -> None:
    from core import text_utils
    from services.conspect_audit_runtime import install_conspect_audit_runtime

    install_conspect_audit_runtime()
    source = (
        "Слово Божьего — нструмент сохранения скелета. "
        "Систематическая проповедь Слово Божьего — единственное средство."
    )
    repaired = text_utils.normalize_common_typos(source)

    assert "Слово Божье — инструмент" in repaired
    assert "проповедь Слова Божьего" in repaired
    assert "нструмент" not in repaired


def test_near_boundary_inline_timestamp_reconciles_section_and_outline() -> None:
    from core import content_audit
    from services.conspect_audit_runtime import install_conspect_audit_runtime

    install_conspect_audit_runtime()
    sections, outline, issues = content_audit.audit_expanded_sections(
        [
            {
                "title": "Сохранение единства",
                "time": "44:06",
                "content": "Начало смыслового узла ⏱ 44:00.",
                "blocks": [],
            }
        ],
        [{"title": "Сохранение единства", "time": "44:06"}],
        label="StudyAnalysis",
    )

    assert sections[0]["time"] == "44:00"
    assert outline[0]["time"] == "44:00"
    assert any(issue.code == "section_time_reconciled" for issue in issues)


def test_large_backward_timestamp_reference_is_not_rewritten() -> None:
    from core import content_audit
    from services.conspect_audit_runtime import install_conspect_audit_runtime

    install_conspect_audit_runtime()
    sections, outline, _issues = content_audit.audit_expanded_sections(
        [
            {
                "title": "Возвращение к раннему примеру",
                "time": "44:06",
                "content": "Сравните с прежней сценой ⏱ 12:00.",
                "blocks": [],
            }
        ],
        [{"title": "Возвращение к раннему примеру", "time": "44:06"}],
        label="StudyAnalysis",
    )

    assert sections[0]["time"] == "44:06"
    assert outline[0]["time"] == "44:06"
