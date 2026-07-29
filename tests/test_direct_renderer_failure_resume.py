from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_direct_wrapper_persists_failure_and_runtime_marker_before_synthesis() -> None:
    source = (
        ROOT
        / "tools"
        / "voxcpm2"
        / "examples"
        / "john_piper_z20py4yqhyq"
        / "voxcpm2_cpu_shorts_production.py"
    ).read_text(encoding="utf-8")

    marker_write = source.index("marker_path.write_text(")
    main_call = source.index("main()", marker_write)
    assert marker_write < main_call
    assert "direct_renderer_failure.json" in source
    assert "_write_failure_report(work_dir, exc)" in source
    assert "Runtime marker intentionally remains" in source


def test_ready_srt_runtime_resumes_and_surfaces_child_root_cause() -> None:
    source = (
        ROOT / "tools" / "voxcpm2" / "generic_clean_direct_runtime.py"
    ).read_text(encoding="utf-8")

    assert "force_fresh=False" in source
    assert "_seed_resumable_clean_marker(root, request)" in source
    assert "direct_cli_runtime.marker.json" in source
    assert "direct_renderer_failure.json" in source
    assert "Прямой VoxCPM2 renderer: {detail}" in source
