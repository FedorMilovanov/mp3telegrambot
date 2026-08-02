from core.title_topic_audit import (
    audit_title_topic_consistency,
    choose_safe_public_title,
    enrich_source_title_context,
    extract_source_title_context,
)


def test_two_term_editorial_title_is_inconclusive_not_an_error() -> None:
    assert audit_title_topic_consistency(
        "Люди Слова",
        "Кризис духовной зрелости и ответственность мужчины за семью",
        [{"topic": "Борьба за веру, святость и семью"}],
    ) is None


def test_metaphorical_two_term_title_is_not_rejected_by_lexical_overlap() -> None:
    assert audit_title_topic_consistency(
        "Узкие врата",
        "Призыв исследовать подлинность обращения и плод покаяния",
        [],
    ) is None


def test_descriptive_title_with_enough_evidence_is_still_audited() -> None:
    issue = audit_title_topic_consistency(
        "История церковной архитектуры Европы",
        "Проповедь о духовной зрелости, семье и стойкости в испытаниях",
        [{"topic": "Бодрствуйте, стойте в вере, будьте мужественны"}],
    )
    assert issue is not None
    assert issue.code == "title_topic_low_overlap"
    assert issue.overlap == 0.0


def test_safe_fallback_requires_measurably_better_topic_alignment() -> None:
    ai_data = {
        "real_title": "История церковной архитектуры Европы",
        "main_topic": "Проповедь о духовной зрелости мужчины и верности семье",
        "timestamps": "21:35 Кризис мужества и ответственность мужчины",
        "title_topic_warning": "low overlap",
    }
    assert choose_safe_public_title(
        ai_data,
        "Духовная зрелость мужчины и верность семье",
    ) == "Духовная зрелость мужчины и верность семье"


def test_source_title_context_preserves_series_session_and_editorial_title() -> None:
    context = extract_source_title_context(
        '[Не от Мира] Сессия 1 - Пол Вошер (#Проповедь 24.04.2021, "Люди Слова")'
    )
    assert context.series == "Не от Мира"
    assert context.session == "Сессия 1"
    assert context.quoted_title == "Люди Слова"


def test_public_title_choice_enriches_context_without_overwriting_ai_fields() -> None:
    ai_data = {
        "real_title": "Люди Слова",
        "real_series": "Редакторский цикл",
    }
    chosen = choose_safe_public_title(
        ai_data,
        '[Не от Мира] Сессия 1 - Пол Вошер (#Проповедь, "Люди Слова")',
    )
    assert chosen == "Люди Слова"
    assert ai_data["real_series"] == "Редакторский цикл"
    assert ai_data["real_session"] == "Сессия 1"
    assert ai_data["source_quoted_title"] == "Люди Слова"


def test_context_enrichment_is_safe_for_none_payload() -> None:
    enriched = enrich_source_title_context(None, "Сессия 2 - Пол Вошер")
    assert enriched["real_session"] == "Сессия 2"
