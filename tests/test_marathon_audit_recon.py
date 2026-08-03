from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name == "nt", reason="Bash reconnaissance runs on Linux CI")
def test_marathon_audit_reconnaissance_report() -> None:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(ROOT)
        if not existing_pythonpath
        else os.pathsep.join((str(ROOT), existing_pythonpath))
    )
    process = subprocess.run(
        ["bash", "tools/marathon_audit.sh"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    report = (process.stdout or "") + (process.stderr or "")
    pytest.fail(
        "Intentional first-pass reconnaissance sentinel. "
        f"Exit={process.returncode}. Full report follows:\n{report}"
    )
