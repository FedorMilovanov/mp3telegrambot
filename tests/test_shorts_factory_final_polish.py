from pathlib import Path

from services.livedub_mix import build_mix_filter
from services import shorts_factory_quality_gate as factory_gate
import services.shorts_subtitle_integrity as subtitle_integrity


def _dialogues(document: str) -> list[str]:
    return [line for line in document.splitlines() if line.startswith("Dialogue:")]


def test_ass_control_characters_cannot_inject_dialogue_events(monkeypatch):
    monkeypatch.setattr(
        "services.shorts_video_impl._pick_subtitle_font",
        lambda: "Arial",
    )
    segments = [
        {
            "start": 0.0,
            "end": 1.0,
            "text": "ignored",
            "words": [
                {
                    "word": "safe\r\nDialogue: 9,0:00:00.00,0:00:09.00,Default,,0,0,0,,evil\x00tail",
                    "start": 0.0,
                    "end": 0.5,
                }
            ],
        }
    ]

    document = subtitle_integrity.generate_ass_from_segments(segments, karaoke=True)

    assert len(_dialogues(document)) == 1
    assert "\r" not in document
    assert "\x00" not in document
    assert "\nDialogue: 9," not in document


def test_one_centisecond_over_karaoke_limit_is_rejected():
    ass = "\n".join(
        [
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            "Dialogue: 0,0:00:01.00,0:00:04.01,Default,,0,0,0,,word",
        ]
    )

    issues = subtitle_integrity.validate_ass_document(ass, karaoke=True)

    assert any("karaoke hold" in issue for issue in issues)


def test_exact_karaoke_limit_is_valid():
    ass = "\n".join(
        [
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            "Dialogue: 0,0:00:01.00,0:00:04.00,Default,,0,0,0,,word",
        ]
    )

    assert subtitle_integrity.validate_ass_document(ass, karaoke=True) == ()


def test_livedub_mix_delays_ru_branch_but_not_original_branch():
    filter_complex = build_mix_filter(
        orig_volume=0.45,
        trans_volume=1.3,
        delay_ms=600,
        duck=False,
    )

    ru_chain = filter_complex.split("[ru0]", 1)[0]
    en_chain = filter_complex.split("[0:a]", 1)[1].split("[en0]", 1)[0]
    assert "adelay=600:all=1" in ru_chain
    assert "adelay=" not in en_chain


def test_factory_orchestration_uses_post_alignment_render_plan_and_single_timeout_owner():
    source = Path("pipelines/shorts_factory.py").read_text(encoding="utf-8")

    render_plan_pos = source.index("render_plan = dict(plan")
    ai_data_pos = source.index("ai_data = factory_ai_data(")
    shorts_send_pos = source.index("shorts_sent = await process_and_send_factory_shorts(")
    longs_send_pos = source.index("longs_sent = await process_and_send_clips(")
    assert render_plan_pos < ai_data_pos < shorts_send_pos < longs_send_pos
    assert 'candidate_kind="short"' in source
    assert 'candidate_kind="long"' in source
    assert "return _factory_livedub_timeout_seconds()" in source
    assert "shorts_sent <= 0" in source
    assert "longs_sent <= 0" in source
    assert "factory_completed_delivery_counts()" not in source
    assert "send_factory_full_translation_if_enabled(" in source
    assert "return bool(shorts_sent or longs_sent or full_video_sent)" in source


def _factory_candidate(title: str, score=None) -> dict:
    item = {
        "title": title,
        "hook": "Сильныйй хак",
        "reason": "Самостоятельная рысль",
        "boundary_verified": True,
        "start_seconds": 10,
        "end_seconds": 100,
    }
    if score is not None:
        item["quality_score"] = score
    return item


def test_factory_invalid_scores_stay_closed_at_zero_threshold(monkeypatch):
    monkeypatch.setenv("SHORTS_FACTORY_MIN_SHORT_SCORE", "0")
    monkeypatch.setenv("SHORTS_FACTORY_MIN_LONG_SCORE", "0")
    nonfinite = _factory_candidate("Nonfinite", float("inf"))
    missing = _factory_candidate("Missing")

    gated = factory_gate.apply_factory_quality_gate(
        {
            "shorts_candidates": [nonfinite, missing],
            "long_candidates": [nonfinite, missing],
        }
    )

    assert gated["shorts_candidates"] == []
    assert gated["long_candidates"] == []


def test_factory_malformed_candidate_collections_fail_closed():
    gated = factory_gate.apply_factory_quality_gate(
        {
            "shorts_candidates": 42,
            "long_candidates": {"unexpected": "mapping"},
        }
    )

    assert gated["shorts_candidates"] == []
    assert gated["long_candidates"] == []
    assert gated["quality_gate"]["shorts_before"] == 0
    assert gated["quality_gate"]["longs_before"] == 0
