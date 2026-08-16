from __future__ import annotations

from pathlib import Path

from services.speech_backends import get_backend
from tools.voxcpm2 import dub_job_preflight


def test_direct_backend_selects_source_owned_russian_only_master() -> None:
    backend = get_backend("voxcpm2")
    repo = Path(__file__).resolve().parents[1]
    runtime = backend.runtime_paths(
        repo,
        {
            "translation_mode": "direct",
            "cpu_venv": r"C:\AI-Archive\VoxCPM2-CPU-TEST\.venv",
            "vox_archive": r"C:\AI-Archive\VoxCPM2-paused-RTX3060",
        },
    )
    assert runtime.master_entrypoint.name == "master_direct_russian_only.py"
    assert runtime.master_module == "tools.voxcpm2.master_direct_russian_only"
    assert runtime.final_qa_module == "tools.voxcpm2.final_media_qa"


def test_preflight_uses_backend_owned_runtime_paths_without_installers() -> None:
    source = Path(dub_job_preflight.__file__).read_text(encoding="utf-8")
    assert "backend.runtime_paths(repo, request)" in source
    assert "backend.process_environment(" in source
    assert "def _runtime_paths(" in source
    assert "def _probe_imports(" in source
    assert "preflight_json_protocol" not in source
    assert "def install" not in source
