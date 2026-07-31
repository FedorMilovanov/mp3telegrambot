from __future__ import annotations

from pathlib import Path

from tools.voxcpm2 import master_monolithic_mix
from tools.voxcpm2 import monolithic_runtime_install


def test_requested_eighteen_percent_becomes_side_bed_with_one_percent_center() -> None:
    levels = master_monolithic_mix.source_bed_levels(0.18)

    assert levels["requested_original_level"] == 0.18
    assert levels["spatial_side_level"] == 0.18
    assert levels["center_full_mix_level"] == 0.010
    assert levels["center_full_mix_level"] < levels["spatial_side_level"] / 10.0


def test_small_requested_bed_scales_center_floor_proportionally() -> None:
    levels = master_monolithic_mix.source_bed_levels(0.04)

    assert levels["spatial_side_level"] == 0.04
    assert levels["center_full_mix_level"] == 0.04 * 0.065


def test_mix_graph_suppresses_center_dialogue_and_keeps_spatial_side(
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

    assert "aformat=channel_layouts=stereo" in graph
    assert "volume=0.010000000[center_floor]" in graph
    assert "pan=stereo|c0=0.5*c0-0.5*c1|c1=0.5*c1-0.5*c0" in graph
    assert "volume=0.180000000[spatial_bed]" in graph
    assert "[original_bed][russian]amix" in graph
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
