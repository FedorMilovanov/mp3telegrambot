from __future__ import annotations

from pathlib import Path

from tools.voxcpm2 import master_monolithic_mix
from tools.voxcpm2 import monolithic_runtime_install


def test_import_immediately_overrides_legacy_mixer_and_calibration() -> None:
    assert (
        master_monolithic_mix._legacy.build_constant_mix
        is master_monolithic_mix.build_dialogue_suppressed_mix
    )
    assert (
        master_monolithic_mix._legacy.calibrate_russian_gain
        is master_monolithic_mix.calibrate_russian_gain
    )


def test_requested_source_level_is_audit_only_and_applied_level_is_zero() -> None:
    levels = master_monolithic_mix.source_bed_levels(0.18)

    assert levels["requested_original_level"] == 0.18
    assert levels["applied_original_level"] == 0.0
    assert levels["spatial_side_level"] == 0.0
    assert levels["center_full_mix_level"] == 0.0
    assert levels["source_bed_applied"] is False
    assert levels["source_bed_disabled_reason"] == (
        "original_mid_and_side_may_both_contain_dialogue"
    )


def test_any_requested_bed_remains_zero_in_direct_ready_srt_mode() -> None:
    for requested in (0.0, 0.04, 0.18, 1.0):
        levels = master_monolithic_mix.source_bed_levels(requested)
        assert levels["requested_original_level"] == requested
        assert levels["applied_original_level"] == 0.0
        assert levels["center_full_mix_level"] == 0.0
        assert levels["spatial_side_level"] == 0.0


def test_mix_graph_uses_only_russian_audio(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        calls.append(command)
        return None

    monkeypatch.setattr(master_monolithic_mix._legacy, "run", fake_run)
    graph = master_monolithic_mix.build_dialogue_suppressed_mix(
        source=tmp_path / "source.mp4",
        mastered_russian=tmp_path / "russian.wav",
        output=tmp_path / "mix.wav",
        source_duration=60.0,
        original_level=0.18,
        russian_gain=1.0,
    )

    assert graph.startswith("[1:a]")
    assert "[0:a]" not in graph
    assert "source_side" not in graph
    assert "spatial_bed" not in graph
    assert "center_floor" not in graph
    assert "volume=1.000000000" in graph
    assert "apad=pad_dur=60.000000" in graph
    assert len(calls) == 1
    command = calls[0]
    assert "-filter_complex" in command
    assert graph in command


def test_runtime_router_selects_exact_monolithic_master(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    renderer = (
        repo
        / "tools"
        / "voxcpm2"
        / "examples"
        / "john_piper_z20py4yqhyq"
        / "voxcpm2_cpu_shorts_production.py"
    )
    master = repo / "tools" / "voxcpm2" / "master_monolithic_mix.py"
    renderer.parent.mkdir(parents=True, exist_ok=True)
    master.parent.mkdir(parents=True, exist_ok=True)
    renderer.write_text("# renderer\n", encoding="utf-8")
    master.write_text("# master\n", encoding="utf-8")

    actual_renderer, actual_master = monolithic_runtime_install._renderer_paths(repo)

    assert actual_renderer == renderer.resolve()
    assert actual_master == master.resolve()


def test_master_command_recognition_is_cross_platform() -> None:
    assert monolithic_runtime_install._is_master_command(
        [r"C:\Python\python.exe", r"C:\repo\tools\voxcpm2\master_monolithic_mix.py"]
    )
    assert monolithic_runtime_install._is_master_command(
        ["/usr/bin/python3", "/repo/tools/voxcpm2/master_monolithic_mix.py"]
    )
    assert monolithic_runtime_install._is_master_command(
        ["python", "/repo/master_constant_mix.py"]
    )
    assert not monolithic_runtime_install._is_master_command(
        ["python", "/repo/voxcpm2_cpu_shorts_production.py"]
    )
