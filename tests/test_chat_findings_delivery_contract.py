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
    assert "select_delivery_file" in source
    assert "pre_subtitle_path" in source
    assert "final_probe.has_video" in source
    assert "final_probe.has_audio" in source


def test_verified_highlights_have_final_render_and_fallback_gate() -> None:
    source = (ROOT / "pipelines" / "montage.py").read_text(encoding="utf-8")
    assert "verify_highlights_delivery" in source
    assert "final delivery QA rejected" in source
    assert "speed_applied" in source
    assert "select_delivery_file" in source
    assert "pre_subtitle_path" in source
    assert "fallback_report" in source


def test_highlights_no_longer_contain_clock_proximity_auto_merge() -> None:
    source = (ROOT / "services" / "highlights_quality.py").read_text(encoding="utf-8")
    assert "def _merge_adjacent_fragments" not in source
    assert "refined = _merge_adjacent_fragments(refined)" not in source
    assert "gap <= 1.6" not in source
    assert '"actual-transcript-highlights-quality-v2"' in source
    assert 'report["reason"] = f"transcription_error:' in source


def test_final_artifact_contract_checks_audio_codec_and_probe_failures() -> None:
    source = (ROOT / "services" / "media_delivery_probe.py").read_text(encoding="utf-8")
    assert 'reasons.append("unexpected_audio_codec")' in source
    assert '"silence_probe_failed"' in source
    assert "class DeliveryFileSelection" in source
    assert "def select_delivery_file" in source
