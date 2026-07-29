from __future__ import annotations

from tools.voxcpm2 import generic_gemini_runtime as creator_vtt


def test_creator_vtt_collapses_rolling_states_but_keeps_repetition(tmp_path) -> None:
    path = tmp_path / "creator.en.vtt"
    path.write_text(
        """WEBVTT

00:00.000 --> 00:02.000
We must
We must remember

00:02.000 --> 00:04.000
No
never
No

00:04.000 --> 00:06.000
<c.green>Grace</c>
<c.green>Grace</c>

""",
        encoding="utf-8",
    )

    cues = creator_vtt.parse_creator_vtt_preserving_text(path)
    assert [cue.text for cue in cues] == [
        "We must remember",
        "No never No",
        "Grace",
    ]


def test_creator_line_merger_ignores_rollback_to_partial_state() -> None:
    assert creator_vtt._merge_creator_caption_lines(
        ["The whole sentence", "The whole", "continues here"]
    ) == "The whole sentence continues here"


def test_creator_line_merger_preserves_meaningful_brackets() -> None:
    assert creator_vtt._merge_creator_caption_lines(
        ["[John 3:16]", "God so loved the world"]
    ) == "[John 3:16] God so loved the world"
    assert creator_vtt._merge_creator_caption_lines(["[Music]"]) == ""


def test_non_adjacent_equal_lines_are_not_globally_deduplicated() -> None:
    assert creator_vtt._merge_creator_caption_lines(
        ["Never", "not once", "Never"]
    ) == "Never not once Never"
