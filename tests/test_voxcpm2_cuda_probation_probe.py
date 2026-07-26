from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "voxcpm2" / "cuda_probation_probe.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "voxcpm2_cuda_probation_probe",
        MODULE_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load_module()


def test_base_report_records_stage_and_debug_environment(monkeypatch) -> None:
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setenv("CUDA_LAUNCH_BLOCKING", "1")
    monkeypatch.setenv("PYTORCH_NO_CUDA_MEMORY_CACHING", "1")

    report = probe.base_report("memory")

    assert report["stage"] == "memory"
    assert report["status"] == "starting"
    assert report["cuda_visible_devices"] == "0"
    assert report["cuda_launch_blocking"] == "1"
    assert report["pytorch_no_cuda_memory_caching"] == "1"


def test_write_report_replaces_file_atomically(tmp_path: Path) -> None:
    path = tmp_path / "stage.report.json"
    probe.write_report(path, {"status": "starting"})
    probe.write_report(path, {"status": "passed", "stage": "init"})

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload == {"status": "passed", "stage": "init"}
    assert not path.with_suffix(path.suffix + ".tmp").exists()
