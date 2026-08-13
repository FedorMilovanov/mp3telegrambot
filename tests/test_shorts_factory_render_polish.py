from pathlib import Path

import pytest

import services.shorts_factory_render_polish as polish


def test_factory_packet_copy_requires_true_unity_speed():
    assert polish.strict_unity_speed_video_copy(normalize_audio=True, speed=1.0)
    assert polish.strict_unity_speed_video_copy(
        normalize_audio=True,
        speed=1.0 + 5e-10,
    )

    for speed in (0.999, 1.001, 0.99, 1.01, float("nan"), float("inf")):
        assert not polish.strict_unity_speed_video_copy(
            normalize_audio=True,
            speed=speed,
        )
    assert not polish.strict_unity_speed_video_copy(
        normalize_audio=False,
        speed=1.0,
    )


def test_factory_long_public_interval_is_fail_closed_at_900_seconds():
    assert polish.validated_factory_long_interval(100.0, 1000.0) == (100.0, 1000.0)
    assert polish.validated_factory_long_interval(0.0, 900.0001) is None
    assert polish.validated_factory_long_interval(-1.0, 100.0) is None
    assert polish.validated_factory_long_interval(10.0, 10.0) is None
    assert polish.validated_factory_long_interval(float("nan"), 20.0) is None
    assert polish.validated_factory_long_interval(0.0, float("inf")) is None


def test_factory_long_silence_snap_cannot_escape_public_end():
    token = polish._LONG_PUBLIC_END.set(1000.0)
    try:
        assert polish.clamp_factory_long_silence_end(1012.0) == 1000.0
        assert polish.clamp_factory_long_silence_end(999.25) == 999.25
    finally:
        polish._LONG_PUBLIC_END.reset(token)

    assert polish.clamp_factory_long_silence_end(1012.0) == 1012.0


@pytest.mark.asyncio
async def test_factory_normalize_copy_failure_retries_canonical_transform(tmp_path):
    input_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    calls = []

    async def copy_transform(source: Path, target: Path) -> bool:
        calls.append(("copy", source, target))
        return False

    async def fallback_transform(
        source: Path,
        target: Path,
        *,
        normalize_audio: bool,
        speed: float,
    ) -> bool:
        calls.append(("fallback", source, target, normalize_audio, speed))
        return True

    assert await polish.normalize_factory_short_with_fallback(
        input_path,
        output_path,
        copy_transform=copy_transform,
        fallback_transform=fallback_transform,
    )
    assert calls == [
        ("copy", input_path, output_path),
        ("fallback", input_path, output_path, True, 1.0),
    ]


@pytest.mark.asyncio
async def test_factory_normalize_copy_success_does_not_add_lossy_fallback(tmp_path):
    calls = []

    async def copy_transform(source: Path, target: Path) -> bool:
        calls.append("copy")
        return True

    async def fallback_transform(*args, **kwargs) -> bool:
        calls.append("fallback")
        return True

    assert await polish.normalize_factory_short_with_fallback(
        tmp_path / "input.mp4",
        tmp_path / "output.mp4",
        copy_transform=copy_transform,
        fallback_transform=fallback_transform,
    )
    assert calls == ["copy"]


@pytest.mark.asyncio
async def test_factory_normalize_cancellation_is_never_converted_to_fallback(tmp_path):
    import asyncio

    calls = []

    async def copy_transform(source: Path, target: Path) -> bool:
        raise asyncio.CancelledError

    async def fallback_transform(*args, **kwargs) -> bool:
        calls.append("fallback")
        return True

    with pytest.raises(asyncio.CancelledError):
        await polish.normalize_factory_short_with_fallback(
            tmp_path / "input.mp4",
            tmp_path / "output.mp4",
            copy_transform=copy_transform,
            fallback_transform=fallback_transform,
        )
    assert calls == []


def test_factory_render_polish_installs_before_disk_guard():
    gate = Path("services/shorts_factory_quality_gate.py").read_text(encoding="utf-8")

    video_pos = gate.index("if not install_factory_video_quality_policy():")
    polish_pos = gate.index("if not install_factory_render_polish():")
    disk_pos = gate.index("if not install_factory_disk_guard():")

    assert video_pos < polish_pos < disk_pos
