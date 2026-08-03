from services.media_delivery_probe import (
    MediaProbe,
    evaluate_highlights_delivery,
    parse_silencedetect,
    resolve_delivery_timing,
)


def test_failed_speed_does_not_shrink_delivery_metadata() -> None:
    timing = resolve_delivery_timing(
        source_start=338.5,
        raw_duration=129.0,
        source_duration=3596.0,
        speed=1.5,
        speed_applied=False,
        final_duration=0.0,
    )
    assert timing.source_end == 467.5
    assert timing.delivery_duration == 129.0
    assert timing.speed_applied is False


def test_successful_speed_uses_measured_final_duration() -> None:
    timing = resolve_delivery_timing(
        source_start=338.5,
        raw_duration=129.0,
        source_duration=3596.0,
        speed=1.5,
        speed_applied=True,
        final_duration=86.12,
    )
    assert timing.source_end == 467.5
    assert timing.delivery_duration == 86.12
    assert timing.speed_applied is True


def test_old_washer_highlights_signature_is_rejected() -> None:
    probe = MediaProbe(
        duration=78.1,
        width=720,
        height=1280,
        audio_sample_rate=96000,
        audio_codec="aac",
        has_video=True,
        has_audio=True,
        size_mb=16.8,
    )
    stderr = """
    silence_start: 41.870979
    silence_end: 47.639667 | silence_duration: 5.768687
    silence_start: 56.400677
    silence_end: 59.297187 | silence_duration: 2.896510
    silence_start: 59.297719
    silence_end: 64.557312 | silence_duration: 5.259594
    """
    intervals = parse_silencedetect(stderr, duration=probe.duration)
    report = evaluate_highlights_delivery(
        probe,
        intervals,
        expected_duration=78.1,
        max_internal_silence=2.8,
    )
    assert intervals[-1] == (56.401, 64.557)
    assert report["accepted"] is False
    assert "unexpected_audio_sample_rate" in report["reasons"]
    assert "long_internal_silence" in report["reasons"]
    assert report["bad_silences"][-1]["duration"] > 8.0


def test_tiny_edge_silence_is_allowed() -> None:
    probe = MediaProbe(
        duration=20.0,
        width=720,
        height=1280,
        audio_sample_rate=48000,
        audio_codec="aac",
        has_video=True,
        has_audio=True,
    )
    report = evaluate_highlights_delivery(
        probe,
        [(0.0, 0.3), (19.7, 20.0)],
        expected_duration=20.0,
        max_internal_silence=2.8,
    )
    assert report["accepted"] is True



def test_non_aac_final_highlights_are_rejected() -> None:
    probe = MediaProbe(
        duration=20.0,
        width=720,
        height=1280,
        audio_sample_rate=48000,
        audio_codec="opus",
        has_video=True,
        has_audio=True,
    )
    report = evaluate_highlights_delivery(
        probe,
        [],
        expected_duration=20.0,
        max_internal_silence=2.8,
    )
    assert report["accepted"] is False
    assert "unexpected_audio_codec" in report["reasons"]
