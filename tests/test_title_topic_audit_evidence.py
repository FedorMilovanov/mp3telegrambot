from core.title_topic_audit import (
    audit_title_topic_consistency,
    choose_safe_public_title,
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
