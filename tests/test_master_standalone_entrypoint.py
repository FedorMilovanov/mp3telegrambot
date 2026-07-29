from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MASTER = (
    ROOT
    / "tools"
    / "voxcpm2"
    / "examples"
    / "john_piper_z20py4yqhyq"
    / "master_constant_mix.py"
)


def test_master_runs_by_absolute_path_without_pythonpath(tmp_path: Path) -> None:
    """Reproduce the worker contract: file path, foreign cwd, isolated env."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    process = subprocess.run(
        [sys.executable, str(MASTER), "--help"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert process.returncode == 0, process.stderr or process.stdout
    assert "--source-video" in process.stdout
    assert "ModuleNotFoundError" not in process.stderr


def test_repo_root_is_established_before_project_import() -> None:
    source = MASTER.read_text(encoding="utf-8")
    bootstrap = source.index("REPO_ROOT = Path(__file__).resolve().parents[4]")
    project_import = source.index("from tools.voxcpm2.final_media_qa import")
    assert bootstrap < project_import
