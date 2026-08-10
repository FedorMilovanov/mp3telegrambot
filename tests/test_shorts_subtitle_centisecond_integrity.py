"""Regression for one-centisecond ASS overlap/flicker."""

from services.shorts_subtitle_integrity import validate_ass_document


def test_one_centisecond_overlap_is_rejected():
    ass = "\n".join(
        [
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            "Dialogue: 0,0:00:01.00,0:00:02.01,Default,,0,0,0,,first",
            "Dialogue: 0,0:00:02.00,0:00:02.50,Default,,0,0,0,,second",
        ]
    )

    issues = validate_ass_document(ass, karaoke=True)

    assert any("overlaps previous event" in issue for issue in issues)


def test_touching_centisecond_boundaries_are_valid():
    ass = "\n".join(
        [
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,first",
            "Dialogue: 0,0:00:02.00,0:00:02.50,Default,,0,0,0,,second",
        ]
    )

    assert validate_ass_document(ass, karaoke=True) == ()
