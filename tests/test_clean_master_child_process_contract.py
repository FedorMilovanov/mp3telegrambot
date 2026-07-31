from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools.voxcpm2 import clean_production_core as core


ROOT = Path(__file__).resolve().parents[1]
MASTER = (
    ROOT
    / "tools"
    / "voxcpm2"
    / "examples"
    / "john_piper_z20py4yqhyq"
    / "master_constant_mix.py"
)


def test_child_environment_places_repo_root_first() -> None:
    env = core._child_python_env({"PYTHONPATH": os.pathsep.join(["old-a", "old-b"])})
    parts = env["PYTHONPATH"].split(os.pathsep)
    assert Path(parts[0]).resolve() == ROOT.resolve()
    assert parts[1:] == ["old-a", "old-b"]
    assert env["PYTHONUTF8"] == "1"
    assert env["PYTHONIOENCODING"] == "utf-8"


def test_real_master_entrypoint_imports_tools_from_foreign_cwd(tmp_path: Path) -> None:
    command = [sys.executable, str(MASTER), "--help"]
    assert core._is_master_command(command) is True
    assert core._is_master_release_command(command) is False

    direct_command = [
        sys.executable,
        str(ROOT / "tools" / "voxcpm2" / "master_monolithic_mix.py"),
        "--help",
    ]
    assert core._is_master_command(direct_command) is True
    assert core._is_master_release_command(direct_command) is False

    result = core._run_child_process(
        command,
        cwd=str(tmp_path),
        env={},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0
    assert "Fixed-English-level Russian Dub master" in (result.stdout or "")
    assert "No module named 'tools'" not in (result.stderr or "")


def test_master_failure_surfaces_exact_stderr_and_releases_to_caller(
    tmp_path: Path,
) -> None:
    script = tmp_path / "master_constant_mix.py"
    script.write_text(
        "import sys\n"
        "sys.stderr.write('MASTER_EXACT_SENTINEL\\n')\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="MASTER_EXACT_SENTINEL") as captured:
        core._run_child_process(
            [sys.executable, str(script)],
            cwd=str(tmp_path),
            env={},
            check=False,
        )
    message = str(captured.value)
    assert "точной причиной" in message
    assert "code 1" not in message


def test_subprocess_proxy_is_scoped_to_clean_legacy_module() -> None:
    assert core._legacy.subprocess is not subprocess
    assert subprocess.run is core._stdlib_subprocess.run
    assert core._legacy.subprocess.run is not subprocess.run
    assert (
        core.CHILD_PYTHON_POLICY
        == "repo-root-pythonpath-master-stderr-and-post-aac-v2"
    )
