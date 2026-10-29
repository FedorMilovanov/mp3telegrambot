import asyncio
from pathlib import Path
from typing import Any

import pytest

import services.livedub_long_qa as long_qa
from services.livedub_long_qa import (
    _coverage_seconds,
    _offset_issues,
    _slice_srt,
    aggregate_segment_results,
    segment_windows,
)


def test_segment_windows_cover_full_thirty_minutes() -> None:
    windows = segment_windows(1800, segment_sec=480, overlap_sec=20)
    assert windows[0] == (0, 480)
    assert windows[-1][0] + windows[-1][1] == 1800
    assert _coverage_seconds(windows) == 1800
    assert len(windows) == 4


def test_segment_windows_keep_boundary_overlap() -> None:
    windows = segment_windows(1000, segment_sec=480, overlap_sec=20)
    assert windows[1][0] == 460
    assert windows[0][0] + windows[0][1] - windows[1][0] == 20


def test_srt_slice_rebases_timestamps_to_segment(tmp_path: Path) -> None:
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


def test_issue_times_are_returned_to_global_timeline() -> None:
    issues = _offset_issues(
        [{"time": "02:15", "severity": "major", "problem": "Ошибка"}],
        460,
    )
    assert issues[0]["time"] == "09:55"


def test_aggregate_merges_overlap_duplicates_and_weights_score() -> None:
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


def test_partial_segment_result_has_its_own_honest_marker() -> None:
    combined = aggregate_segment_results(
        [(0, 480, {"score": 95, "issues": []})],
        total_windows=4,
        duration=1800,
    )
    assert combined is not None
    assert combined["_segmented_partial"] is True
    assert "_low_confidence" not in combined
    assert combined["_coverage_ratio"] < 0.5


def test_missing_original_confidence_marker_is_preserved_separately() -> None:
    combined = aggregate_segment_results(
        [(0, 480, {"score": 95, "issues": [], "_low_confidence": True})],
        total_windows=1,
        duration=480,
    )
    assert combined is not None
    assert combined["_low_confidence"] is True
    assert "_segmented_partial" not in combined


def test_segmented_long_qa_uses_existing_local_sources(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    video = tmp_path / "dub.mp4"
    original = tmp_path / "original.mp3"
    russian = tmp_path / "clean-ru.mp3"
    srt = tmp_path / "dub.srt"
    for path in (video, original, russian, srt):
        path.write_bytes(b"media")
    monkeypatch.setenv("LIVEDUB_LONG_QA_THRESHOLD_SEC", "120")
    extracted_from: list[Path] = []
    sliced_from: list[Path] = []

    def extract(source: Path, _start: float, _length: float, output: Path) -> Path:
        extracted_from.append(source)
        output.write_bytes(b"segment")
        return output

    def slice_srt(source: Path, _start: float, _length: float, _output: Path) -> None:
        sliced_from.append(source)
        return None

    monkeypatch.setattr(long_qa, "_extract_audio_segment", extract)
    monkeypatch.setattr(long_qa, "_slice_srt", slice_srt)
    calls: list[dict[str, Any]] = []

    async def base_runner(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"score": 100, "issues": []}

    result = asyncio.run(
        long_qa.run_long_translation_qa(
            base_runner,
            dub_video_path=video,
            original_audio_path=original,
            ai_data=None,
            duration=121,
            model_name="model",
            dub_srt_path=srt,
            dub_audio_path=russian,
        )
    )

    assert result is not None
    assert result["_segments_checked"] == 1
    assert extracted_from == [original, russian]
    assert sliced_from == [srt]
    assert calls[0]["dub_audio_path"] is not None
    assert calls[0]["dub_audio_path"].name == "dub-01.mp3"


def test_long_qa_without_original_uses_full_qa_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LIVEDUB_LONG_QA_THRESHOLD_SEC", "120")
    video = tmp_path / "dub.mp4"
    video.write_bytes(b"video")
    calls: list[dict[str, Any]] = []

    async def base_runner(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"score": 100, "issues": []}

    result = asyncio.run(
        long_qa.run_long_translation_qa(
            base_runner,
            dub_video_path=video,
            original_audio_path=None,
            ai_data=None,
            duration=121,
            model_name="model",
        )
    )

    assert result == {"score": 100, "issues": []}
    assert len(calls) == 1
    assert calls[0]["dub_video_path"] == video
    assert calls[0]["original_audio_path"] is None


def test_long_qa_below_threshold_uses_base_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LIVEDUB_LONG_QA_THRESHOLD_SEC", "120")
    video = tmp_path / "dub.mp4"
    video.write_bytes(b"video")
    calls: list[dict[str, Any]] = []

    async def base_runner(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"score": 88, "issues": []}

    result = asyncio.run(
        long_qa.run_long_translation_qa(
            base_runner,
            dub_video_path=video,
            original_audio_path=None,
            ai_data=None,
            duration=120,
            model_name="model",
        )
    )

    assert result == {"score": 88, "issues": []}
    assert len(calls) == 1


def test_long_qa_all_failed_segments_use_one_full_qa_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LIVEDUB_LONG_QA_THRESHOLD_SEC", "120")
    video = tmp_path / "dub.mp4"
    original = tmp_path / "original.mp3"
    russian = tmp_path / "clean-ru.mp3"
    for path in (video, original, russian):
        path.write_bytes(b"media")

    def extract(_source: Path, _start: float, _length: float, output: Path) -> Path:
        output.write_bytes(b"segment")
        return output

    monkeypatch.setattr(long_qa, "_extract_audio_segment", extract)
    calls: list[dict[str, Any]] = []

    async def base_runner(**kwargs: Any) -> dict[str, Any] | None:
        calls.append(kwargs)
        if len(calls) == 1:
            return None
        return {"score": 77, "issues": []}

    result = asyncio.run(
        long_qa.run_long_translation_qa(
            base_runner,
            dub_video_path=video,
            original_audio_path=original,
            ai_data=None,
            duration=121,
            model_name="model",
            dub_audio_path=russian,
        )
    )

    assert result == {"score": 77, "issues": []}
    assert len(calls) == 2
    assert calls[-1]["original_audio_path"] == original
    assert calls[-1]["dub_audio_path"] == russian


def test_long_qa_source_default_is_high_thinking() -> None:
    source = Path("services/livedub_long_qa.py").read_text(encoding="utf-8")

    assert 'os.getenv("LIVEDUB_LONG_QA_THINKING", "high").strip() or "high"' in source
    assert 'os.getenv("LIVEDUB_LONG_QA_THINKING", "low")' not in source
