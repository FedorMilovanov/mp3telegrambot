from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import json
from pathlib import Path
import threading
import time
from typing import Any

import pytest

from handlers import dub_health
from services import dub_studio_runtime
from tools.voxcpm2 import clean_runtime_contract
from tools.voxcpm2 import dub_job_preflight as preflight
from tools.voxcpm2 import generic_project_runtime


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = "dub-0123456789"


def _patch_studio(monkeypatch, studio: Path) -> Path:
    studio = studio.resolve()
    monkeypatch.setattr(preflight._legacy, "studio_root", lambda: studio)
    monkeypatch.setattr(generic_project_runtime._legacy, "studio_root", lambda: studio)
    return studio / "projects" / PROJECT_ID


def _request(**overrides):
    return {
        "schema_version": 1,
        "video_id": "AbCdEf12345",
        "source_url": "https://youtube.com/watch?v=AbCdEf12345",
        "translation_mode": "direct",
        "cpu_venv": "C:/AI/VoxCPM/.venv",
        "vox_archive": "C:/AI/VoxCPM/archive",
        **overrides,
    }


def test_preflight_import_resolves_to_v2_compatibility_package() -> None:
    assert Path(preflight.__file__).name == "__init__.py"
    assert preflight.POLICY == "dub-production-preflight-v2"
    assert preflight.REPORT_SCHEMA == 2
    assert preflight.PREFLIGHT_HEARTBEAT_SECONDS == 5.0
    assert preflight._legacy.run is preflight.run


def test_new_project_shared_recipe_root_normalizes_to_project_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    studio = tmp_path / "studio"
    expected = _patch_studio(monkeypatch, studio)

    for legacy_root in (studio, studio / "projects"):
        project = {
            "id": PROJECT_ID,
            "recipe_id": "generic_short_v1",
            "work_root": str(legacy_root),
        }
        assert preflight._project_root(project) == expected.resolve()

    exact = {
        "id": PROJECT_ID,
        "recipe_id": "generic_short_v1",
        "work_root": str(expected),
    }
    assert preflight._project_root(exact) == expected.resolve()


def test_preflight_rejects_cross_project_work_root(monkeypatch, tmp_path: Path) -> None:
    studio = tmp_path / "studio"
    _patch_studio(monkeypatch, studio)
    other = studio / "projects" / "dub-aaaaaaaaaa"
    with pytest.raises(RuntimeError, match="canonical project ID"):
        preflight._project_root(
            {
                "id": PROJECT_ID,
                "recipe_id": "generic_short_v1",
                "work_root": str(other),
            }
        )


def test_runtime_paths_use_strict_project_request(monkeypatch, tmp_path: Path) -> None:
    studio = tmp_path / "studio"
    root = _patch_studio(monkeypatch, studio)
    root.mkdir(parents=True)
    request_path = root / "request.json"
    project = {
        "id": PROJECT_ID,
        "recipe_id": "generic_short_v1",
        "work_root": str(studio),
    }

    request_path.write_text(json.dumps(_request(schema_version=True)), encoding="utf-8")
    with pytest.raises(RuntimeError, match="bool"):
        preflight._runtime_paths(project)

    request_path.write_text(json.dumps(_request()), encoding="utf-8")
    paths = preflight._runtime_paths(project)
    assert paths["root"] == root.resolve()
    assert paths["request"] == request_path.resolve()
    assert paths["master"].name == "master_constant_mix.py"
    assert paths["renderer"].name == "voxcpm2_cpu_shorts_production.py"


def test_report_schema_is_exact_not_bool(tmp_path: Path) -> None:
    path = tmp_path / "production_preflight.json"
    path.write_text(json.dumps({"schema_version": True}), encoding="utf-8")
    assert preflight._read_report(path) == {}

    payload = {"schema_version": 2, "passed": True}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert preflight._read_report(path) == payload


def test_render_custom_runs_preflight_and_writes_action_specific_report(
    monkeypatch,
    tmp_path: Path,
) -> None:
    paths = {"root": tmp_path}
    written: list[tuple[Path, dict]] = []
    monkeypatch.setattr(preflight, "_runtime_paths", lambda _project: paths)
    monkeypatch.setattr(
        preflight,
        "_signature",
        lambda _paths, *, action: {"action": action, "implementation": "sha"},
    )
    monkeypatch.setattr(preflight, "_read_report", lambda _path: {})
    monkeypatch.setattr(preflight, "_probe_imports", lambda _paths: {"python": "ok"})
    monkeypatch.setattr(
        preflight,
        "_preflight_heartbeat",
        lambda _project_id, _action: nullcontext(),
    )
    monkeypatch.setattr(
        preflight,
        "_atomic_json",
        lambda path, payload: written.append((Path(path), dict(payload))),
    )

    result = preflight.run(
        {"id": PROJECT_ID, "recipe_id": "generic_short_v1"},
        "render_custom",
    )
    assert result["passed"] is True
    assert result["skipped"] is False
    assert result["action"] == "render_custom"
    assert result["signature"]["action"] == "render_custom"
    assert written[0][0] == tmp_path / "output" / "production_preflight.json"
    assert written[0][1] == result


def test_cache_never_crosses_project_or_action() -> None:
    signature = {"implementation": "same"}
    current = {
        "schema_version": 2,
        "policy": preflight.POLICY,
        "passed": True,
        "skipped": False,
        "project_id": PROJECT_ID,
        "action": "render_direct",
        "signature": signature,
        "probe": {},
    }
    assert preflight._cache_hit(
        current,
        project_id=PROJECT_ID,
        action="render_direct",
        signature=signature,
    )
    assert not preflight._cache_hit(
        current,
        project_id=PROJECT_ID,
        action="repair_audio",
        signature=signature,
    )
    assert not preflight._cache_hit(
        current,
        project_id="dub-aaaaaaaaaa",
        action="render_direct",
        signature=signature,
    )


def test_atomic_preflight_reports_do_not_share_temp_names(tmp_path: Path) -> None:
    destination = tmp_path / "production_preflight.json"

    def write(index: int) -> None:
        preflight._atomic_json(
            destination,
            {"schema_version": 2, "index": index, "value": "ok"},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(24)))

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["value"] == "ok"
    assert payload["index"] in range(24)
    assert not list(tmp_path.glob("production_preflight.json.tmp.*"))


def test_signature_covers_all_clean_modules_and_both_preflight_layers() -> None:
    identity = preflight._implementation_identity(ROOT)
    files = identity["files"]
    assert "tools/voxcpm2/dub_job_preflight.py" in files
    assert "tools/voxcpm2/dub_job_preflight/__init__.py" in files
    assert "tools/voxcpm2/clean_production_core/__init__.py" in files
    assert "tools/voxcpm2/clean_source_download/__init__.py" in files
    assert len(identity["sha256"]) == 64


def test_signature_uses_complete_model_and_voxcpm_runtime_fingerprints(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cpu_python = tmp_path / "python.exe"
    renderer = tmp_path / "renderer.py"
    master = tmp_path / "master.py"
    for path in (cpu_python, renderer, master):
        path.write_bytes(b"runtime")

    model_manifest = {
        "path": str(tmp_path / "model"),
        "artifacts": [{"name": "weights.safetensors", "sha256": "weight-sha"}],
    }
    runtime_manifest = {
        "module": str(tmp_path / "voxcpm" / "__init__.py"),
        "versions": {"voxcpm": "1.0", "torch": "2.0"},
        "python_files": [{"path": "model.py", "sha256": "runtime-sha"}],
    }
    calls: list[tuple[str, Path]] = []

    def fake_model_manifest(path: Path) -> dict[str, Any]:
        calls.append(("model", Path(path)))
        return model_manifest

    def fake_runtime(path: Path) -> dict[str, Any]:
        calls.append(("runtime", Path(path)))
        return runtime_manifest

    monkeypatch.setattr(clean_runtime_contract, "_model_manifest", fake_model_manifest)
    monkeypatch.setattr(clean_runtime_contract, "_voxcpm_runtime", fake_runtime)
    monkeypatch.setattr(
        preflight,
        "_implementation_identity",
        lambda _repo: {"files": {"core.py": "sha"}, "sha256": "impl-sha"},
    )
    monkeypatch.setattr(
        preflight,
        "_executable_identity",
        lambda name: {"path": f"/{name}", "size": 1, "mtime_ns": 1},
    )
    monkeypatch.setattr(preflight, "_sha256", lambda path: f"sha:{Path(path).name}")

    signature = preflight._signature(
        {
            "repo": ROOT,
            "cpu_python": cpu_python,
            "archive": tmp_path / "archive",
            "renderer": renderer,
            "master": master,
        },
        action="render_direct",
    )
    assert signature["model"] == model_manifest
    assert signature["voxcpm_runtime"] == runtime_manifest
    assert signature["implementation"]["sha256"] == "impl-sha"
    assert signature["action"] == "render_direct"
    assert calls == [
        ("model", (tmp_path / "archive").resolve()),
        ("runtime", cpu_python.resolve()),
    ]


def test_preflight_heartbeat_pulses_immediately_and_stops(monkeypatch) -> None:
    pulses: list[dict[str, Any]] = []
    first_pulse = threading.Event()

    class FakeStore:
        def worker_heartbeat(self, worker_id: str, **kwargs: Any) -> None:
            pulses.append({"worker_id": worker_id, **kwargs})
            first_pulse.set()

    monkeypatch.setattr(
        preflight,
        "_claimed_job_context",
        lambda _project_id: (FakeStore(), 42, "worker-1"),
    )

    with preflight._preflight_heartbeat(PROJECT_ID, "render_direct"):
        assert first_pulse.wait(timeout=1.0)
        assert pulses
        latest = pulses[-1]
        assert latest["worker_id"] == "worker-1"
        assert latest["status"] == "busy"
        assert latest["current_job_id"] == 42
        assert latest["details"]["runtime"] == "dub-worker-quality-v4.6"
        assert latest["details"]["stage"] == "preflight"
        assert latest["details"]["action"] == "render_direct"

    completed_count = len(pulses)
    time.sleep(0.05)
    assert len(pulses) == completed_count


def test_health_import_synchronizes_supervisor_and_worker_contract() -> None:
    assert dub_health._WORKER_RUNTIME == "dub-worker-quality-v4.6"
    assert dub_health._legacy._WORKER_RUNTIME == "dub-worker-quality-v4.6"
    assert dub_studio_runtime._WORKER_RUNTIME == "dub-worker-quality-v4.6"

    worker_source = (
        ROOT / "tools" / "voxcpm2" / "dub_worker_hardened.py"
    ).read_text(encoding="utf-8")
    assert '_RUNTIME_VERSION = "dub-worker-quality-v4.6"' in worker_source
    assert "worker.execute_job = _execute_job_with_preflight" in worker_source
