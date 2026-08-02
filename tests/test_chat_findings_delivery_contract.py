from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_shorts_delivery_uses_measured_timeline_and_actual_trim_range() -> None:
    source = (ROOT / "pipelines" / "shorts.py").read_text(encoding="utf-8")
    assert "probe_media_async(raw_path)" in source
    assert "delivery_duration" in source
    assert "start_seconds=timing.source_start" in source
    assert "end_seconds=timing.source_end" in source
    assert 'duration=max(1, int(round(delivery_duration)))' in source
    assert 'duration=int(c["duration_seconds"])' not in source


def test_verified_highlights_have_final_render_gate() -> None:
    source = (ROOT / "pipelines" / "montage.py").read_text(encoding="utf-8")
    assert "verify_highlights_delivery" in source
    assert "final delivery QA rejected" in source
    assert "speed_applied" in source


def test_highlights_no_longer_auto_merge_by_clock_proximity() -> None:
    source = (ROOT / "services" / "highlights_quality.py").read_text(encoding="utf-8")
    assert "refined = _merge_adjacent_fragments(refined)" not in source
    assert '"actual-transcript-highlights-quality-v2"' in source
    assert 'report["reason"] = f"transcription_error:' in source
