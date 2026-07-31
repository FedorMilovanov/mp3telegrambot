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
    source = np.column_stack(
        (
            source_voice + rng.normal(0.0, 0.018, count),
            source_voice + rng.normal(0.0, 0.018, count),
        )
    )
    russian_voice = (
        0.20 * np.sin(2.0 * np.pi * 171.0 * time + 0.7)
        + 0.040 * np.sin(2.0 * np.pi * 342.0 * time + 0.4)
    )
    russian = np.column_stack((russian_voice, russian_voice))
    return rng, source, russian


def _spatial_mix(
    source: np.ndarray,
    russian: np.ndarray,
    *,
    requested: float,
    russian_gain: float,
) -> np.ndarray:
    levels = spatial_bed_contract.source_bed_levels(requested)
    center = levels["center_full_mix_level"]
    side = levels["spatial_side_level"]
    return np.column_stack(
        (
            russian[:, 0] * russian_gain
            + source[:, 0] * center
            + (source[:, 0] - source[:, 1]) * 0.5 * side,
            russian[:, 1] * russian_gain
            + source[:, 1] * center
            + (source[:, 1] - source[:, 0]) * 0.5 * side,
        )
    )


def test_post_aac_spatial_bed_accepts_suppressed_center_and_requested_side() -> None:
    rng, source, russian = _fixture()
    mixed = _spatial_mix(source, russian, requested=0.18, russian_gain=1.07)
    mixed += rng.normal(0.0, 0.00008, mixed.shape)

    report = final_media_qa.estimate_spatial_bed(
        source,
        mixed,
        russian,
        expected_level=0.18,
        sample_rate=8_000,
    )

    assert report["passed"] is True, report
    assert abs(report["estimated_center_level"] - 0.010) < 0.004
    assert abs(report["estimated_russian_gain"] - 1.07) < 0.01
    assert report["normalized_residual"] < 0.01


def test_old_full_eighteen_percent_source_bed_is_rejected_as_dialogue_leak() -> None:
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


def test_mono_source_uses_center_floor_without_inventing_side_requirement() -> None:
    _rng, source, russian = _fixture()
    mono = np.column_stack((source[:, 0], source[:, 0]))
    mixed = _spatial_mix(mono, russian, requested=0.18, russian_gain=0.98)

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
    assert abs(report["estimated_center_level"] - 0.010) < 0.004


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
