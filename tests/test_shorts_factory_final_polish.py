from pathlib import Path

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


def test_factory_orchestration_uses_post_alignment_render_plan_and_single_timeout_owner():
    source = Path("pipelines/shorts_factory.py").read_text(encoding="utf-8")

    render_plan_pos = source.index("render_plan = dict(plan)")
    ai_data_pos = source.index("ai_data = factory_ai_data(")
    assert render_plan_pos < ai_data_pos
    assert 'candidate_kind="short"' in source
    assert 'candidate_kind="long"' in source
    assert "return _factory_livedub_timeout_seconds()" in source
    assert "factory_completed_delivery_counts()" in source
    assert "return bool(shorts_sent or longs_sent)" in source
