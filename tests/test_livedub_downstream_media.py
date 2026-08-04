from pathlib import Path
from types import SimpleNamespace

import pytest

import services.shorts_factory_media as media
from services.shorts_factory_media import (
    align_livedub_candidate,
    align_livedub_interval,
    align_livedub_montage_candidates,
    livedub_downstream_envelope,
    probe_livedub_source_duration,
)


def test_livedub_envelope_uses_full_mix_tail(monkeypatch):
    monkeypatch.setenv("LIVEDUB_DELAY_MS", "600")
    monkeypatch.setenv("LIVEDUB_TAIL_MARGIN_MS", "1000")
    monkeypatch.setenv("LIVEDUB_DOWNSTREAM_PREROLL_SEC", "0.25")
    monkeypatch.setenv("LIVEDUB_DOWNSTREAM_TAIL_EXTRA_SEC", "0.15")

    assert livedub_downstream_envelope() == (0.25, 1.75)
    assert align_livedub_interval(
        10,
        100,
        source_duration=300,
        public_max_seconds=180,
    ) == (9.75, 101.75)


def test_livedub_short_rejects_interval_without_room_for_required_tail(monkeypatch):
    monkeypatch.setenv("LIVEDUB_DELAY_MS", "600")
    monkeypatch.setenv("LIVEDUB_TAIL_MARGIN_MS", "1000")

    assert align_livedub_interval(
        10,
        189,
        source_duration=300,
        public_max_seconds=180,
    ) is None


def test_livedub_candidate_keeps_public_labels_but_changes_render_numbers(monkeypatch):
    monkeypatch.setenv("LIVEDUB_DELAY_MS", "600")
    monkeypatch.setenv("LIVEDUB_TAIL_MARGIN_MS", "1000")
    candidate = {
        "start": "0:10",
        "end": "1:40",
        "start_seconds": 10,
        "end_seconds": 100,
        "duration_seconds": 90,
        "title": "Мысль",
    }

    aligned = align_livedub_candidate(
        candidate,
        source_duration=300,
        public_max_seconds=180,
    )

    assert aligned is not None
    assert aligned["start"] == "0:10"
    assert aligned["end"] == "1:40"
    assert aligned["start_seconds"] < 10
    assert aligned["end_seconds"] > 100
    assert aligned["livedub_semantic_start_seconds"] == 10
    assert aligned["livedub_semantic_end_seconds"] == 100
    assert candidate["start_seconds"] == 10


def test_livedub_montage_expands_every_fragment_and_recalculates_total(monkeypatch):
    monkeypatch.setenv("LIVEDUB_DELAY_MS", "600")
    monkeypatch.setenv("LIVEDUB_TAIL_MARGIN_MS", "1000")
    candidates = [
        {
            "title": "Montage",
            "total_dur": 20,
            "fragments": [
                {"start_seconds": 10, "end_seconds": 20},
                {"start_seconds": 100, "end_seconds": 110},
            ],
        }
    ]

    aligned = align_livedub_montage_candidates(candidates, source_duration=300)

    assert len(aligned) == 1
    assert len(aligned[0]["fragments"]) == 2
    assert aligned[0]["total_dur"] > 20
    assert aligned[0]["fragments"][0]["start_seconds"] < 10
    assert aligned[0]["fragments"][1]["end_seconds"] > 110
    assert candidates[0]["total_dur"] == 20


@pytest.mark.asyncio
async def test_livedub_source_duration_uses_exact_deliverable_probe(monkeypatch, tmp_path):
    source = tmp_path / "livedub.mp4"
    source.write_bytes(b"x" * 2048)
    probe = SimpleNamespace(duration=901.234)

    async def fake_probe(path):
        assert path == source
        return probe

    monkeypatch.setattr(media, "probe_media_async", fake_probe)
    monkeypatch.setattr(media, "media_probe_is_deliverable", lambda value: value is probe)

    assert await probe_livedub_source_duration(
        source,
        fallback_duration=900,
    ) == 901.234


@pytest.mark.asyncio
async def test_livedub_source_probe_is_fail_closed_by_default(monkeypatch, tmp_path):
    source = tmp_path / "broken.mp4"
    source.write_bytes(b"x" * 2048)

    async def fake_probe(_path):
        return None

    monkeypatch.delenv("LIVEDUB_DOWNSTREAM_REQUIRE_PROBE", raising=False)
    monkeypatch.setattr(media, "probe_media_async", fake_probe)
    monkeypatch.setattr(media, "media_probe_is_deliverable", lambda _value: False)

    with pytest.raises(RuntimeError, match="обязательный media probe"):
        await probe_livedub_source_duration(source, fallback_duration=900)


@pytest.mark.asyncio
async def test_livedub_probe_fallback_requires_explicit_degraded_opt_out(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "broken.mp4"
    source.write_bytes(b"x" * 2048)

    async def fake_probe(_path):
        return None

    monkeypatch.setenv("LIVEDUB_DOWNSTREAM_REQUIRE_PROBE", "0")
    monkeypatch.setattr(media, "probe_media_async", fake_probe)
    monkeypatch.setattr(media, "media_probe_is_deliverable", lambda _value: False)

    assert await probe_livedub_source_duration(
        source,
        fallback_duration=900,
    ) == 900


def test_runtime_policy_wires_every_requested_cut_mode_without_import_side_effect():
    source = Path("services/shorts_factory_media.py").read_text(encoding="utf-8")
    timing_source = Path("services/shorts_factory_timing.py").read_text(
        encoding="utf-8"
    )
    runtime_source = Path("services/shorts_factory_runtime.py").read_text(
        encoding="utf-8"
    )

    assert "shorts_module.process_and_send_shorts = process_shorts" in source
    assert "clips_module.process_and_send_clips = process_clips" in source
    assert "clips_module.render_clip = verified_render_clip" in source
    assert "montage_module.process_and_send_montage = process_montage" in source
    assert "montage_module.process_and_send_highlights = process_highlights" in source
    assert "main_pipeline_module.process_and_send_highlights = process_highlights" in source
    assert "media_probe_is_deliverable(probe)" in source
    assert "\ninstall_livedub_downstream_media_policy()\n" not in source
    assert "\ninstall_livedub_downstream_media_policy()\n" not in timing_source
    assert "if not install_livedub_downstream_media_policy():" in runtime_source
