from core.content_audit import audit_expanded_sections
from core.synopsis_timestamps import (
    reconcile_synopsis_timestamps,
    section_anchor_seconds,
    unresolved_timestamp_issues,
)


def test_reconciles_section_start_to_earliest_own_anchor_and_outline() -> None:
    sections = [
        {"title": "Первый", "time": "0:00", "content": "Начало ⏱ 0:10."},
        {
            "title": "Второй",
            "time": "12:00",
            "content": "Переход ⏱ 10:34. Развитие ⏱ 11:20.",
        },
    ]
    outline = [
        {"title": "Первый", "time": "0:00"},
        {"title": "Второй", "time": "12:00"},
    ]

    reconciled, final_outline, issues = reconcile_synopsis_timestamps(sections, outline)

    assert reconciled[1]["time"] == "10:34"
    assert final_outline[1]["time"] == "10:34"
    assert [item.code for item in issues] == ["section_time_reconciled"]
    assert unresolved_timestamp_issues(reconciled) == []


def test_collects_block_text_steps_and_explicit_timestamp_fields() -> None:
    section = {
        "content": "Абзац ⏱ 20:00",
        "anchor_timestamp": "19:45",
        "blocks": [
            {
                "text": "Тезис 📌 19:30",
                "steps": ["Шаг ⏱ 19:20"],
                "timestamp": "19:10",
            }
        ],
    }
    assert section_anchor_seconds(section) == (1150, 1160, 1170, 1185, 1200)


def test_scripture_reference_without_marker_is_not_video_anchor() -> None:
    section = {
        "content": "Иеремия 12:5 и 1 Коринфянам 16:13–14.",
        "blocks": [{"text": "Иоанна 3:16"}],
    }
    assert section_anchor_seconds(section) == ()


def test_cross_boundary_anchor_is_reported_and_not_guessed() -> None:
    sections = [
        {"title": "Первый", "time": "10:00", "content": "⏱ 10:05"},
        {"title": "Второй", "time": "12:00", "content": "⏱ 9:50"},
    ]

    reconciled, final_outline, issues = reconcile_synopsis_timestamps(sections)

    assert reconciled[1]["time"] == "12:00"
    assert final_outline[1]["time"] == "12:00"
    assert [item.code for item in issues] == ["section_time_reconcile_blocked"]
    assert unresolved_timestamp_issues(reconciled)[0].code == "inline_timestamp_before_section"


def test_missing_section_start_is_recovered_from_explicit_anchor() -> None:
    sections = [{"title": "Раздел", "time": "", "content": "Точка входа ⏱ 5:40"}]
    reconciled, final_outline, issues = reconcile_synopsis_timestamps(sections, [])
    assert reconciled[0]["time"] == "5:40"
    assert final_outline == [{"title": "Раздел", "time": "5:40"}]
    assert issues[0].before == ""


def test_reconciliation_is_idempotent() -> None:
    sections = [{"title": "Раздел", "time": "8:00", "content": "⏱ 7:34"}]
    first_sections, first_outline, first_issues = reconcile_synopsis_timestamps(sections)
    second_sections, second_outline, second_issues = reconcile_synopsis_timestamps(
        first_sections,
        first_outline,
    )
    assert first_sections == second_sections
    assert first_outline == second_outline
    assert [item.code for item in first_issues] == ["section_time_reconciled"]
    assert second_issues == []


def test_outline_time_never_overrides_reconciled_section_time() -> None:
    sections = [{"title": "Раздел", "time": "9:00", "content": "⏱ 8:34"}]
    outline = [{"title": "Старое название", "time": "9:00"}]
    reconciled, final_outline, _ = reconcile_synopsis_timestamps(sections, outline)
    assert reconciled[0]["time"] == "8:34"
    assert final_outline == [{"title": "Раздел", "time": "8:34"}]


def test_synopsis_content_audit_returns_one_reconciled_timeline() -> None:
    sections = [
        {"title": "Первый", "time": "0:00", "content": "Начало ⏱ 0:00."},
        {"title": "Второй", "time": "12:00", "content": "Переход ⏱ 10:34."},
    ]
    outline = [
        {"title": "Первый", "time": "0:00"},
        {"title": "Второй", "time": "12:00"},
    ]

    audited_sections, audited_outline, issues = audit_expanded_sections(
        sections,
        outline,
        label="SynopsisDensityRetry",
        expected_author="Пол Вошер",
    )

    assert audited_sections[1]["time"] == "10:34"
    assert audited_outline[1] == {"title": "Второй", "time": "10:34"}
    assert any(item.code == "section_time_reconciled" for item in issues)
