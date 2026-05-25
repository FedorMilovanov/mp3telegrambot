from core.candidate_schema import CandidateValidationReport, validate_candidate_times
from core.core_utils import time_to_seconds


def test_candidate_validation_report_counts_reasons():
    report = CandidateValidationReport()
    report.accept()
    report.reject("too_short")
    report.reject("too_short")
    report.reject("bad_time_format")
    assert report.accepted == 1
    assert report.rejected == 3
    assert report.reasons == {"too_short": 2, "bad_time_format": 1}
    assert report.total == 4
    assert "accepted=1" in report.summary()


def test_validate_candidate_times_accepts_good_range():
    ok, reason, start, end, dur = validate_candidate_times(
        "1:05", "2:10", duration=300, min_sec=30, max_sec=90,
        time_to_seconds=time_to_seconds,
    )
    assert ok is True
    assert reason == "ok"
    assert (start, end, dur) == (65, 130, 65)


def test_validate_candidate_times_rejects_bad_ranges():
    cases = [
        ("", "1:00", "missing_time"),
        ("bad", "1:00", "bad_time_format"),
        ("1:00", "0:59", "end_before_start"),
        ("1:00", "4:00", "end_after_duration"),
        ("1:00", "1:10", "too_short"),
    ]
    for start_raw, end_raw, expected in cases:
        ok, reason, *_ = validate_candidate_times(
            start_raw, end_raw, duration=180, min_sec=30, max_sec=120,
            time_to_seconds=time_to_seconds,
        )
        assert ok is False
        assert reason == expected

    ok, reason, *_ = validate_candidate_times(
        "1:00", "3:30", duration=300, min_sec=30, max_sec=120,
        time_to_seconds=time_to_seconds,
    )
    assert ok is False
    assert reason == "too_long"
