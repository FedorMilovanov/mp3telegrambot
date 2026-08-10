"""Contract tests for source-SRT evidence used by translated Factory cuts."""

import services.shorts_factory_timing as timing
from services.translation_editorial import parse_srt


def test_parse_srt_exposes_text_consumed_by_boundary_proof(tmp_path):
    source = tmp_path / "source.en.srt"
    source.write_text(
        "1\n"
        "00:00:01,000 --> 00:00:03,000\n"
        "Christ is risen.\n\n"
        "2\n"
        "00:00:04,000 --> 00:00:09,000\n"
        "<i>[Music]</i>\n\n",
        encoding="utf-8",
    )

    cues = parse_srt(source)

    assert len(cues) == 2
    assert getattr(cues[0], "text", "") == "Christ is risen."
    assert timing._caption_cue_is_lexical_speech(cues[0].text) is True
    assert timing._caption_cue_is_lexical_speech(cues[1].text) is False
