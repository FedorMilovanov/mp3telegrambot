"""Regression coverage for glitch-free Shorts ASS karaoke timing."""

from services.shorts_subtitle_integrity import (
    generate_ass_from_segments,
    validate_ass_document,
)


def _dialogues(document: str) -> list[str]:
    return [line for line in document.splitlines() if line.startswith("Dialogue:")]


def test_long_pause_after_preposition_is_a_real_chunk_boundary():
    segments = [
        {
            "start": 0.0,
            "end": 5.0,
            "text": "Мы в Христе свободны.",
            "words": [
                {"word": "Мы", "start": 0.00, "end": 0.20},
                {"word": "в", "start": 0.25, "end": 0.35},
                {"word": "Христе", "start": 4.00, "end": 4.45},
                {"word": "свободны.", "start": 4.55, "end": 5.00},
            ],
        }
    ]

    document = generate_ass_from_segments(segments, karaoke=True)

    assert validate_ass_document(document, karaoke=True) == ()
    lines = _dialogues(document)
    assert len(lines) == 4
    # The word "в" must end with its real speech, not remain highlighted until 4s.
    assert ",0:00:00.25,0:00:00.35," in lines[1]
    assert ",0:00:04.00," in lines[2]


def test_overlapping_whisper_words_are_normalized_to_monotonic_events():
    segments = [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "Вера имеет объект",
            "words": [
                {"word": "Вера", "start": 0.00, "end": 0.70},
                {"word": "имеет", "start": 0.45, "end": 0.80},
                {"word": "объект", "start": 0.75, "end": 1.20},
            ],
        }
    ]

    document = generate_ass_from_segments(segments, karaoke=True)

    assert validate_ass_document(document, karaoke=True) == ()
    assert len(_dialogues(document)) == 3


def test_zero_duration_whisper_word_is_given_safe_minimum_interval():
    segments = [
        {
            "start": 0.0,
            "end": 1.0,
            "text": "Истина",
            "words": [
                {"word": "Истина", "start": 0.50, "end": 0.50},
            ],
        }
    ]

    document = generate_ass_from_segments(segments, karaoke=True)

    assert validate_ass_document(document, karaoke=True) == ()
    assert ",0:00:00.50,0:00:00.58," in _dialogues(document)[0]


def test_remote_hyphen_particle_does_not_merge_across_silence():
    segments = [
        {
            "start": 0.0,
            "end": 4.0,
            "text": "кто то сказал",
            "words": [
                {"word": "кто", "start": 0.00, "end": 0.30},
                {"word": "-то", "start": 2.00, "end": 2.20},
                {"word": "сказал", "start": 2.30, "end": 2.80},
            ],
        }
    ]

    document = generate_ass_from_segments(segments, karaoke=True)

    assert validate_ass_document(document, karaoke=True) == ()
    lines = _dialogues(document)
    assert len(lines) == 3
    assert ",0:00:00.00,0:00:00.30," in lines[0]
    assert ",0:00:02.00," in lines[1]


def test_remote_punctuation_does_not_extend_previous_word_hold():
    segments = [
        {
            "start": 0.0,
            "end": 3.0,
            "text": "Истина. Далее",
            "words": [
                {"word": "Истина", "start": 0.00, "end": 0.40},
                {"word": ".", "start": 1.80, "end": 1.90},
                {"word": "Далее", "start": 2.10, "end": 2.50},
            ],
        }
    ]

    document = generate_ass_from_segments(segments, karaoke=True)

    assert validate_ass_document(document, karaoke=True) == ()
    lines = _dialogues(document)
    assert ",0:00:00.00,0:00:00.40," in lines[0]
    assert "Истина." in lines[0]
    assert ",0:00:02.10," in lines[1]


def test_validator_rejects_abnormally_long_karaoke_hold():
    document = """[Script Info]
ScriptType: v4.00+
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:04.50,Default,,0,0,0,,word
"""

    issues = validate_ass_document(document, karaoke=True)

    assert any("karaoke hold" in issue for issue in issues)


def test_validator_rejects_overlapping_dialogue_events():
    document = """[Script Info]
ScriptType: v4.00+
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,one
Dialogue: 0,0:00:00.90,0:00:01.20,Default,,0,0,0,,two
"""

    issues = validate_ass_document(document, karaoke=True)

    assert any("overlaps previous event" in issue for issue in issues)
