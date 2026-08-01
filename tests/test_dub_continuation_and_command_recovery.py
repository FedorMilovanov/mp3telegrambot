from __future__ import annotations

from pathlib import Path

from handlers.dub_multicommand import (
    MULTICOMMAND_POLICY,
    STALE_CARD_POLICY,
    parse_dub_command_lines,
)
from tools.voxcpm2 import direct_max_quality_render
from tools.voxcpm2.direct_source_prosody import _defer_short_continuation
from tools.voxcpm2.direct_timeline_compaction import (
    MIN_COMPACTION_GAP_SECONDS,
    POLICY,
    TARGET_GAP_SECONDS,
    compact_timeline_segments,
)

ROOT = Path(__file__).resolve().parents[1]


def _evidence(_path: Path) -> dict[str, float | bool]:
    return {
        "available": True,
        "active_start": 0.05,
        "active_end": 0.95,
    }


def test_two_dub_commands_in_one_message_are_both_parsed() -> None:
    parsed = parse_dub_command_lines("/dubworker\n/dubrun dub-bfa6ffa01b")
    assert parsed == [
        ("dubworker", []),
        ("dubrun", ["dub-bfa6ffa01b"]),
    ]


def test_bot_username_blank_lines_and_normal_fallback() -> None:
    parsed = parse_dub_command_lines(
        "/dubcheck@PreachingMP3Bot\n\n/dubstatus@PreachingMP3Bot last"
    )
    assert parsed == [
        ("dubcheck", []),
        ("dubstatus", ["last"]),
    ]
    assert parse_dub_command_lines("/dubworker") == []
    assert parse_dub_command_lines("/dubworker\n/not_a_dub_command") == []
    assert MULTICOMMAND_POLICY == "dub-multiline-command-dispatch-v1"
    assert STALE_CARD_POLICY == "dub-callback-edit-or-reply-v1"


def test_candidate_stage_defers_only_repairable_short_continuation() -> None:
    result = _defer_short_continuation(
        {
            "cadence": "continuation",
            "hard_ok": False,
            "failures": ["continuation_too_short"],
            "penalty": 64.0,
        }
    )
    assert result["hard_ok"] is True
    assert result["failures"] == []
    assert result["timeline_compaction_required"] is True
    assert result["penalty"] == 64.0


def test_candidate_stage_never_hides_wrong_ending() -> None:
    result = _defer_short_continuation(
        {
            "cadence": "continuation",
            "hard_ok": False,
            "failures": ["continuation_too_short", "continuation_closes"],
        }
    )
    assert result["hard_ok"] is False
    assert result["failures"] == ["continuation_closes"]


def test_short_colon_cue_can_be_diagnosed_by_compaction_utility() -> None:
    fitted = [
        (
            {
                "id": 7,
                "text": "Двадцать пятый стих:",
                "start": 10.0,
                "end": 13.0,
                "tail_guard": 0.22,
                "start_delay_ms": 0,
            },
            Path("segment-7.wav"),
        ),
        (
            {
                "id": 8,
                "text": "Она облечена силою и достоинством.",
                "start": 13.0,
                "end": 15.4,
                "tail_guard": 0.22,
                "start_delay_ms": 0,
            },
            Path("segment-8.wav"),
        ),
    ]

    adjusted, report = compact_timeline_segments(fitted, evidence_reader=_evidence)
    first = adjusted[0][0]
    second = adjusted[1][0]
    audible_end = float(first["start"]) + 0.95

    assert first["timeline_compaction_policy"] == POLICY
    assert float(first["start"]) > 10.0
    assert float(first["end"]) == 13.0
    assert abs(float(second["start"]) - 13.0) < 1e-9
    assert abs(float(second["start"]) - audible_end - TARGET_GAP_SECONDS) < 1e-6
    assert report["shifted_segment_ids"] == [7]


def test_natural_continuation_gap_is_not_retimed() -> None:
    def evidence(path: Path) -> dict[str, float | bool]:
        if path.name == "segment-1.wav":
            return {
                "available": True,
                "active_start": 0.05,
                "active_end": 1.70,
            }
        return _evidence(path)

    fitted = [
        (
            {
                "id": 1,
                "text": "И вот что сказано:",
                "start": 0.0,
                "end": 2.0,
                "tail_guard": 0.22,
            },
            Path("segment-1.wav"),
        ),
        (
            {
                "id": 2,
                "text": "Следующая часть мысли.",
                "start": 2.0,
                "end": 4.0,
                "tail_guard": 0.22,
            },
            Path("segment-2.wav"),
        ),
    ]

    adjusted, report = compact_timeline_segments(fitted, evidence_reader=evidence)
    assert MIN_COMPACTION_GAP_SECONDS == 0.32
    assert abs(2.0 - 1.70) < MIN_COMPACTION_GAP_SECONDS
    assert float(adjusted[0][0]["start"]) == 0.0
    assert report["shifted_segment_ids"] == []


def test_terminal_cue_is_never_retimed() -> None:
    fitted = [
        (
            {
                "id": 1,
                "text": "Это законченная мысль.",
                "start": 0.0,
                "end": 3.0,
                "tail_guard": 0.22,
            },
            Path("segment-1.wav"),
        ),
        (
            {
                "id": 2,
                "text": "Следующая мысль.",
                "start": 3.0,
                "end": 5.0,
                "tail_guard": 0.22,
            },
            Path("segment-2.wav"),
        ),
    ]

    adjusted, report = compact_timeline_segments(fitted, evidence_reader=_evidence)
    assert float(adjusted[0][0]["start"]) == 0.0
    assert report["shifted_segment_ids"] == []


def test_runtime_fingerprints_compaction_but_production_keeps_source_timeline() -> None:
    contract = (
        ROOT / "tools" / "voxcpm2" / "clean_runtime_contract" / "__init__.py"
    ).read_text(encoding="utf-8")
    supervisor = (
        ROOT / "services" / "dub_studio_runtime" / "__init__.py"
    ).read_text(encoding="utf-8")

    assert '"tools/voxcpm2/direct_source_prosody/__init__.py"' in contract
    assert '"tools/voxcpm2/direct_timeline_compaction.py"' in contract
    assert direct_max_quality_render.TIMELINE_COMPACTION_POLICY == (
        "no-late-shift-monolithic-assembly-v2"
    )
    assert callable(direct_max_quality_render.build_timeline)
    assert "_legacy_install_dub_studio_runtime()" in supervisor
    assert "register_dub_multicommand_handler(application)" in supervisor
