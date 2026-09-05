from pathlib import Path

from services.livedub_long_qa import (
    _coverage_seconds,
    _offset_issues,
    _slice_srt,
    aggregate_segment_results,
    segment_windows,
)


def test_segment_windows_cover_full_thirty_minutes():
    windows = segment_windows(1800, segment_sec=480, overlap_sec=20)
    assert windows[0] == (0, 480)
    assert windows[-1][0] + windows[-1][1] == 1800
    assert _coverage_seconds(windows) == 1800
    assert len(windows) == 4


def test_segment_windows_keep_boundary_overlap():
    windows = segment_windows(1000, segment_sec=480, overlap_sec=20)
    assert windows[1][0] == 460
    assert windows[0][0] + windows[0][1] - windows[1][0] == 20


def test_srt_slice_rebases_timestamps_to_segment(tmp_path: Path):
    source = tmp_path / "full.srt"
    source.write_text(
        "1\n00:07:55,000 --> 00:08:05,000\nГраница первого сегмента\n\n"
        "2\n00:10:00,000 --> 00:10:04,000\nСередина второго сегмента\n\n"
        "3\n00:16:00,000 --> 00:16:04,000\nЗа пределами сегмента\n",
        encoding="utf-8",
    )
    output = tmp_path / "part.srt"
    result = _slice_srt(source, 460, 480, output)
    assert result == output
    text = output.read_text(encoding="utf-8")
    assert "00:00:15,000 --> 00:00:25,000" in text
    assert "00:02:20,000 --> 00:02:24,000" in text
    assert "За пределами" not in text


def test_issue_times_are_returned_to_global_timeline():
    issues = _offset_issues(
        [{"time": "02:15", "severity": "major", "problem": "Ошибка"}],
        460,
    )
    assert issues[0]["time"] == "09:55"


def test_aggregate_merges_overlap_duplicates_and_weights_score():
    results = [
        (
            0,
            480,
            {
                "score": 90,
                "issues": [
                    {
                        "time": "07:50",
                        "severity": "minor",
                        "problem": "Неверно передан термин оправдание",
                        "heard": "улучшение",
                        "should_be": "оправдание",
                    }
                ],
            },
        ),
        (
            460,
            480,
            {
                "score": 80,
                "issues": [
                    {
                        "time": "00:12",
                        "severity": "major",
                        "problem": "Неверно передан термин оправдание",
                        "heard": "улучшение",
                        "should_be": "оправдание",
                    }
                ],
            },
        ),
        (920, 480, {"score": 100, "issues": []}),
        (1380, 420, {"score": 100, "issues": []}),
    ]
    combined = aggregate_segment_results(results, total_windows=4, duration=1800)
    assert combined is not None
    assert combined["_segments_checked"] == 4
    assert combined["_coverage_ratio"] == 1.0
    assert len(combined["issues"]) == 1
    assert combined["issues"][0]["severity"] == "major"
    assert combined["issues"][0]["time"] in {"07:50", "07:52"}
    assert 90 <= combined["score"] <= 95


def test_partial_segment_result_has_its_own_honest_marker():
    combined = aggregate_segment_results(
        [(0, 480, {"score": 95, "issues": []})],
        total_windows=4,
        duration=1800,
    )
    assert combined is not None
    assert combined["_segmented_partial"] is True
    assert "_low_confidence" not in combined
    assert combined["_coverage_ratio"] < 0.5


def test_missing_original_confidence_marker_is_preserved_separately():
    combined = aggregate_segment_results(
        [(0, 480, {"score": 95, "issues": [], "_low_confidence": True})],
        total_windows=1,
        duration=480,
    )
    assert combined is not None
    assert combined["_low_confidence"] is True
    assert "_segmented_partial" not in combined


def test_long_qa_source_default_is_high_thinking():
    source = Path("services/livedub_long_qa.py").read_text(encoding="utf-8")

    assert 'os.getenv("LIVEDUB_LONG_QA_THINKING", "high").strip() or "high"' in source
    assert 'os.getenv("LIVEDUB_LONG_QA_THINKING", "low")' not in source
