from pathlib import Path

from services.media_delivery_probe import (
    MediaProbe,
    evaluate_highlights_delivery,
    media_probe_is_deliverable,
    parse_silencedetect,
    resolve_delivery_timing,
    select_delivery_file,
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


def _write_megabytes(path: Path, size_mb: float) -> None:
    path.write_bytes(b"x" * max(1, int(size_mb * 1024 * 1024)))


def test_delivery_selector_prefers_valid_primary(tmp_path: Path) -> None:
    primary = tmp_path / "with_subtitles.mp4"
    fallback = tmp_path / "without_subtitles.mp4"
    _write_megabytes(primary, 0.02)
    _write_megabytes(fallback, 0.01)

    selection = select_delivery_file(
        primary,
        fallback,
        max_size_mb=1.0,
    )

    assert selection.path == primary
    assert selection.selected == "primary"
    assert selection.reason == "primary_accepted"


def test_delivery_selector_recovers_from_optional_subtitle_overflow(tmp_path: Path) -> None:
    primary = tmp_path / "with_subtitles.mp4"
    fallback = tmp_path / "without_subtitles.mp4"
    _write_megabytes(primary, 0.03)
    _write_megabytes(fallback, 0.01)

    selection = select_delivery_file(
        primary,
        fallback,
        max_size_mb=0.02,
    )

    assert selection.path == fallback
    assert selection.selected == "fallback"
    assert selection.reason == "fallback_after_primary_oversize"
    assert selection.primary_size_mb > 0.02
    assert selection.fallback_size_mb <= 0.02


def test_delivery_selector_fails_closed_for_missing_files_or_bad_limit(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "missing.mp4"
    fallback = tmp_path / "empty.mp4"
    fallback.touch()

    no_file = select_delivery_file(primary, fallback, max_size_mb=10.0)
    bad_limit = select_delivery_file(primary, fallback, max_size_mb=0.0)

    assert no_file.path is None
    assert no_file.selected == "none"
    assert "primary_missing_or_empty" in no_file.reason
    assert bad_limit.path is None
    assert bad_limit.reason == "no_usable_file_after_primary_invalid_size_limit"


def test_delivery_probe_requires_real_video_audio_evidence() -> None:
    accepted = MediaProbe(
        duration=10.0,
        width=720,
        height=1280,
        audio_sample_rate=48000,
        audio_codec="aac",
        has_video=True,
        has_audio=True,
    )
    corrupt_subtitle_artifact = MediaProbe(
        duration=10.0,
        width=720,
        height=1280,
        audio_sample_rate=0,
        audio_codec="",
        has_video=True,
        has_audio=False,
    )

    assert media_probe_is_deliverable(accepted) is True
    assert media_probe_is_deliverable(corrupt_subtitle_artifact) is False
    assert media_probe_is_deliverable(None) is False


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


def test_silencedetect_parser_ignores_malformed_marker_lines() -> None:
    stderr = """
    silence_start:
    silence_end:
    silence_start: 3.0
    silence_end: 6.5 | silence_duration: 3.5
    """
    assert parse_silencedetect(stderr, duration=10.0) == [(3.0, 6.5)]


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
