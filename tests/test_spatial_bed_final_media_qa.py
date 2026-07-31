from __future__ import annotations

import numpy as np

from tools.voxcpm2 import final_media_qa
from tools.voxcpm2 import spatial_bed_contract


def _fixture(*, seconds: float = 8.0, sample_rate: int = 8_000):
    rng = np.random.default_rng(20260731)
    count = int(seconds * sample_rate)
    time = np.arange(count, dtype=np.float64) / sample_rate
    source_voice = (
        0.16 * np.sin(2.0 * np.pi * 145.0 * time)
        + 0.045 * np.sin(2.0 * np.pi * 290.0 * time)
    )
    # Speech-bearing side: the two channels have different voice coloration.
    source = np.column_stack(
        (
            source_voice + 0.035 * np.sin(2.0 * np.pi * 190.0 * time)
            + rng.normal(0.0, 0.012, count),
            source_voice - 0.030 * np.sin(2.0 * np.pi * 190.0 * time + 0.2)
            + rng.normal(0.0, 0.012, count),
        )
    )
    russian_voice = (
        0.20 * np.sin(2.0 * np.pi * 171.0 * time + 0.7)
        + 0.040 * np.sin(2.0 * np.pi * 342.0 * time + 0.4)
    )
    russian = np.column_stack((russian_voice, russian_voice))
    return rng, source, russian


def test_post_aac_accepts_russian_only_mix_for_nonzero_requested_setting() -> None:
    rng, source, russian = _fixture()
    mixed = russian * 1.07 + rng.normal(0.0, 0.00008, russian.shape)

    report = final_media_qa.estimate_spatial_bed(
        source,
        mixed,
        russian,
        expected_level=0.18,
        sample_rate=8_000,
    )

    assert report["passed"] is True, report
    assert abs(report["estimated_center_level"]) < 0.004
    assert abs(report["estimated_side_level"] or 0.0) < 0.004
    assert abs(report["estimated_russian_gain"] - 1.07) < 0.01
    assert report["expected"]["requested_original_level"] == 0.18
    assert report["expected"]["applied_original_level"] == 0.0
    assert report["normalized_residual"] < 0.01


def test_old_full_eighteen_percent_source_bed_is_rejected() -> None:
    rng, source, russian = _fixture()
    unsafe = russian * 1.04 + source * 0.18
    unsafe += rng.normal(0.0, 0.00008, unsafe.shape)

    report = final_media_qa.estimate_spatial_bed(
        source,
        unsafe,
        russian,
        expected_level=0.18,
        sample_rate=8_000,
    )

    assert report["passed"] is False
    assert report["estimated_center_level"] > 0.15
    assert any("центр исходной речи не подавлен" in item for item in report["failures"])


def test_old_side_only_bed_is_rejected_when_side_contains_speech() -> None:
    rng, source, russian = _fixture()
    side = (source[:, 0] - source[:, 1]) * 0.5
    unsafe = russian * 1.02 + np.column_stack((side * 0.18, -side * 0.18))
    unsafe += rng.normal(0.0, 0.00005, unsafe.shape)

    report = final_media_qa.estimate_spatial_bed(
        source,
        unsafe,
        russian,
        expected_level=0.18,
        sample_rate=8_000,
    )

    assert report["passed"] is False
    assert abs(report["estimated_side_level"] or 0.0) > 0.15
    assert any("side=" in item for item in report["failures"])


def test_mono_source_does_not_create_a_source_requirement() -> None:
    _rng, source, russian = _fixture()
    mono = np.column_stack((source[:, 0], source[:, 0]))
    mixed = russian * 0.98

    report = final_media_qa.estimate_spatial_bed(
        mono,
        mixed,
        russian,
        expected_level=0.18,
        sample_rate=8_000,
    )

    assert report["passed"] is True, report
    assert report["side_measurement_applicable"] is False
    assert report["estimated_side_level"] is None
    assert abs(report["estimated_center_level"]) < 0.004


def test_zero_requested_bed_remains_zero_safe() -> None:
    rng, source, russian = _fixture()
    mixed = russian * 1.02 + rng.normal(0.0, 0.00004, russian.shape)

    report = final_media_qa.estimate_spatial_bed(
        source,
        mixed,
        russian,
        expected_level=0.0,
        sample_rate=8_000,
    )

    assert report["passed"] is True, report
    assert abs(report["estimated_center_level"]) < 0.004
    assert report["estimated_russian_gain"] > 1.0
    assert spatial_bed_contract.POLICY == "russian-only-direct-master-v2"
