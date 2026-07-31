from __future__ import annotations

import os
from pathlib import Path

from services.speech_backends import (
    BACKEND_CONTRACT_POLICY,
    BACKEND_RUNTIME_PATH_POLICY,
    default_backend,
)
from tools.voxcpm2 import preflight_json_protocol


def test_voxcpm2_backend_owns_exact_monolithic_runtime_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    request = {
        "cpu_venv": str(tmp_path / "cpu-venv"),
        "vox_archive": str(tmp_path / "archive"),
    }

    runtime = default_backend().runtime_paths(repo, request)

    expected_python = Path(request["cpu_venv"]) / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    example = repo / "tools" / "voxcpm2" / "examples" / "john_piper_z20py4yqhyq"
    assert BACKEND_CONTRACT_POLICY == "speech-backend-contract-v1"
    assert BACKEND_RUNTIME_PATH_POLICY == "speech-backend-runtime-paths-v1"
    assert runtime.backend_id == "voxcpm2"
    assert runtime.cpu_python == expected_python.resolve()
    assert runtime.archive_root == Path(request["vox_archive"]).resolve()
    assert runtime.renderer_entrypoint == (
        example / "voxcpm2_cpu_shorts_production.py"
    ).resolve()
    assert runtime.master_entrypoint == (
        repo / "tools" / "voxcpm2" / "master_monolithic_mix.py"
    ).resolve()
    assert runtime.renderer_module in runtime.import_modules
    assert runtime.master_module == "tools.voxcpm2.master_monolithic_mix"
    assert runtime.master_module in runtime.import_modules
    assert runtime.final_qa_module in runtime.import_modules
    assert {"voxcpm", "torch", "soundfile"}.issubset(runtime.import_modules)


def test_runtime_paths_report_is_json_ready(tmp_path: Path) -> None:
    runtime = default_backend().runtime_paths(
        tmp_path,
        {
            "cpu_venv": str(tmp_path / "venv"),
            "vox_archive": str(tmp_path / "archive"),
        },
    )

    payload = runtime.as_dict()

    assert payload["backend_id"] == "voxcpm2"
    assert payload["runtime_path_policy"] == "speech-backend-runtime-paths-v1"
    assert isinstance(payload["import_modules"], list)
    assert isinstance(payload["cpu_python"], str)
    assert payload["master_module"] == "tools.voxcpm2.master_monolithic_mix"


def test_preflight_installer_routes_both_paths_and_probe() -> None:
    from tools.voxcpm2 import dub_job_preflight

    old_paths = dub_job_preflight._runtime_paths
    old_probe = dub_job_preflight._probe_imports
    try:
        preflight_json_protocol.install()
        assert dub_job_preflight._runtime_paths is preflight_json_protocol.runtime_paths
        assert dub_job_preflight._probe_imports is preflight_json_protocol.probe_imports
    finally:
        dub_job_preflight._runtime_paths = old_paths
        dub_job_preflight._probe_imports = old_probe
