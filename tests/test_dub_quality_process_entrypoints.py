from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools.voxcpm2.generic_audio_repair_runtime_bootstrap import (
    RepairSubprocessDiagnostics,
)


def test_quality_renderer_runs_as_file_outside_repo_cwd(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    renderer = repo / "tools" / "voxcpm2" / "voxcpm2_quality_v4_renderer.py"
    original = (
        repo
        / "tools"
        / "voxcpm2"
        / "examples"
        / "john_piper_z20py4yqhyq"
        / "voxcpm2_cpu_shorts_production.py"
    )
    env = dict(os.environ)
    env["VOXCPM_ORIGINAL_RENDERER"] = str(original)

    result = subprocess.run(
        [sys.executable, str(renderer), "--help"],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, (result.stdout or "") + (result.stderr or "")
    assert "--segments-json" in result.stdout


def test_repair_diagnostics_preserve_exact_child_failure(tmp_path: Path) -> None:
    log_path = tmp_path / "audio_repair_child.log"
    diagnostics = RepairSubprocessDiagnostics(subprocess, log_path)

    with pytest.raises(RuntimeError, match="INNER ROOT CAUSE") as error:
        diagnostics.run(
            [
                sys.executable,
                "-c",
                "print('before failure'); print('INNER ROOT CAUSE'); raise SystemExit(7)",
            ],
            cwd=tmp_path,
            check=False,
        )

    assert "кодом 7" in str(error.value)
    saved = log_path.read_text(encoding="utf-8")
    assert "INNER ROOT CAUSE" in saved
    assert "code=7" in saved
