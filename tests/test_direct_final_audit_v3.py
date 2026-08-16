from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


SCRIPT = r'''
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from tools.voxcpm2 import direct_final_audit_v3 as final
from tools.voxcpm2 import direct_retry_epoch as retry
from tools.voxcpm2 import direct_timing_guard as guard


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)

    def write_payload(name, payload):
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    base = {
        "id": 1,
        "text": "Короткая русская фраза.",
        "start": 0.0,
        "end": 4.0,
        "tail_guard": 0.18,
        "reference_profile": "extended",
    }
    valid = write_payload("valid.json", [base])
    values = final._raw_segments(valid)
    assert values[0]["id"] == 1

    for index, value in enumerate(
        (
            {**base, "id": True},
            {**base, "id": 1.5},
            {**base, "start": True},
            {**base, "start_delay_ms": 1.5},
        ),
        1,
    ):
        try:
            final._raw_segments(write_payload(f"bad-{index}.json", [value]))
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"raw invalid segment accepted: {value!r}")

    model = root / "models" / "snapshot-abc"
    model.mkdir(parents=True)
    (model / "config.json").write_text('{"model":"test"}', encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"weights")
    context = final._model_context(model, digest)
    assert context["direct_model_snapshot"] == "snapshot-abc"
    assert context["direct_model_config_sha256"] == digest(model / "config.json")
    assert len(context["direct_model_snapshot_fingerprint"]) == 64

    try:
        retry.load_retry_epoch(root / "retry", 1.5)
    except RuntimeError:
        pass
    else:
        raise AssertionError("fractional retry segment_id was accepted")

    timing = root / "timing_blocks"
    timing.mkdir(parents=True)
    marker = timing / "segment_01.json"
    marker.write_text("{}", encoding="utf-8")
    for index in range(12):
        (timing / f"segment_01.json.stale-test-{index:02d}").write_text(
            str(index), encoding="utf-8"
        )
    guard._prune_marker_archives(timing, marker.name, limit=final.MAX_ARCHIVED_MARKERS)
    assert len(list(timing.glob("segment_01.json.stale-*"))) == final.MAX_ARCHIVED_MARKERS

    final_source = (Path.cwd() / "tools" / "voxcpm2" / "direct_final_audit_v3.py").read_text(encoding="utf-8")
    timing_source = (Path.cwd() / "tools" / "voxcpm2" / "direct_timing_guard.py").read_text(encoding="utf-8")
    retry_source = (Path.cwd() / "tools" / "voxcpm2" / "direct_retry_epoch.py").read_text(encoding="utf-8")
    entrypoint = (Path.cwd() / "tools" / "voxcpm2" / "direct_max_quality_cli.py").read_text(encoding="utf-8")

    assert "def install_final_audit" not in final_source
    assert "install_final_audit(globals())" not in entrypoint
    assert "def _prune_marker_archives" in timing_source
    assert "def _strict_segment_id" in retry_source
    assert "direct_surgical_guard" not in entrypoint

print("source-owned final audit contracts: 8 checks passed")
'''


def test_final_direct_audit_contracts_in_isolated_process() -> None:
    repo = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(repo), environment.get("PYTHONPATH", "")) if value
    )
    process = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        cwd=repo,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        check=False,
    )
    details = (process.stdout or "") + (process.stderr or "")
    assert process.returncode == 0, details
    assert "8 checks passed" in details
