from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from handlers import dub_health
from services import dub_studio_runtime
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


def test_health_import_synchronizes_supervisor_and_worker_contract() -> None:
    assert dub_health._WORKER_RUNTIME == "dub-worker-quality-v4.6"
    assert dub_health._legacy._WORKER_RUNTIME == "dub-worker-quality-v4.6"
    assert dub_studio_runtime._WORKER_RUNTIME == "dub-worker-quality-v4.6"

    worker_source = (
        ROOT / "tools" / "voxcpm2" / "dub_worker_hardened.py"
    ).read_text(encoding="utf-8")
    assert '_RUNTIME_VERSION = "dub-worker-quality-v4.6"' in worker_source
    assert "worker.execute_job = _execute_job_with_preflight" in worker_source
