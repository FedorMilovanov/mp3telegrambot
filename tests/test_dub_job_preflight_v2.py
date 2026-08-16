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
from services.dub_worker_release import WORKER_RUNTIME
from tools.voxcpm2 import clean_runtime_contract
from tools.voxcpm2 import dub_job_preflight as preflight
from tools.voxcpm2 import generic_project_runtime


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = "dub-0123456789"


def _patch_studio(monkeypatch: pytest.MonkeyPatch, studio: Path) -> Path:
    studio = studio.resolve()
    monkeypatch.setattr(preflight, "studio_root", lambda: studio)
    monkeypatch.setattr(generic_project_runtime, "studio_root", lambda: studio)
    return studio / "projects" / PROJECT_ID


def _request(**overrides: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "video_id": "AbCdEf12345",
        "source_url": "https://youtube.com/watch?v=AbCdEf12345",
        "translation_mode": "direct",
        "cpu_venv": "C:/AI/VoxCPM/.venv",
        "vox_archive": "C:/AI/VoxCPM/archive",
        **overrides,
    }


def test_preflight_import_resolves_to_v2_package() -> None:
    assert Path(preflight.__file__).name == "__init__.py"
    assert preflight.POLICY == "dub-production-preflight-v2"
    assert preflight.REPORT_SCHEMA == 2
    assert preflight.PREFLIGHT_HEARTBEAT_SECONDS == 5.0
    assert preflight.run is preflight.run


def test_project_root_is_canonical_and_cross_project_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    studio = tmp_path / "studio"
    expected = _patch_studio(monkeypatch, studio)
    for legacy_root in (studio, studio / "projects", expected):
        project = {
            "id": PROJECT_ID,
            "recipe_id": "generic_short_v1",
            "work_root": str(legacy_root),
        }
        assert preflight._project_root(project) == expected.resolve()

    other = studio / "projects" / "dub-aaaaaaaaaa"
    with pytest.raises(RuntimeError, match="canonical project ID"):
        preflight._project_root(
            {
                "id": PROJECT_ID,
                "recipe_id": "generic_short_v1",
                "work_root": str(other),
            }
        )


def test_runtime_paths_use_strict_project_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
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
    assert paths["renderer"].name == "voxcpm2_cpu_shorts_production.py"


def test_render_custom_runs_preflight_and_writes_action_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    written: list[tuple[Path, dict[str, Any]]] = []
    monkeypatch.setattr(preflight, "_runtime_paths", lambda _project: {"root": tmp_path})
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
    assert result["action"] == "render_custom"
    assert written[0][0] == tmp_path / "output" / "production_preflight.json"


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


def test_signature_covers_clean_modules_and_preflight_layers() -> None:
    identity = preflight._implementation_identity(ROOT)
    files = identity["files"]
    assert "tools/voxcpm2/dub_job_preflight.py" in files
    assert "tools/voxcpm2/dub_job_preflight/__init__.py" in files
    assert "tools/voxcpm2/clean_production_core/__init__.py" in files
    assert "tools/voxcpm2/clean_source_download/__init__.py" in files
    assert len(identity["sha256"]) == 64
    assert callable(clean_runtime_contract.build_fingerprints)


def test_preflight_heartbeat_uses_shared_worker_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        latest = pulses[-1]
        assert latest["worker_id"] == "worker-1"
        assert latest["status"] == "busy"
        assert latest["current_job_id"] == 42
        assert latest["details"]["runtime"] == WORKER_RUNTIME
        assert latest["details"]["stage"] == "preflight"

    completed_count = len(pulses)
    time.sleep(0.05)
    assert len(pulses) == completed_count


def test_health_and_supervisor_share_worker_release() -> None:
    assert dub_health._WORKER_RUNTIME == WORKER_RUNTIME
    assert dub_health._WORKER_RUNTIME == WORKER_RUNTIME
    assert dub_studio_runtime._WORKER_RUNTIME == WORKER_RUNTIME
    assert dub_studio_runtime._WORKER_RUNTIME == WORKER_RUNTIME
