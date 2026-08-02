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
from tools.voxcpm2 import direct_surgical_guard
from tools.voxcpm2 import direct_surgical_polish_v2
from tools.voxcpm2 import direct_timing_guard as guard


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)
    events = []
    reader_calls = []

    def read_segments(path):
        reader_calls.append(str(path))
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        result = []
        for position, source in enumerate(payload, 1):
            item = dict(source)
            item["id"] = int(item.get("id", position))
            item["start"] = float(item["start"])
            item["end"] = float(item["end"])
            item["tail_guard"] = float(item.get("tail_guard", 0.18))
            item["text"] = str(item.get("text") or "").strip()
            item["speech_slot"] = item["end"] - item["start"] - item["tail_guard"]
            item["reference_profile"] = str(item.get("reference_profile") or "extended")
            result.append(item)
        return result

    def prepare_reference(source, output, sf_module):
        del sf_module
        events.append("prepare")
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(source).read_bytes())
        return {"sha256": digest(target), "duration": 8.0}

    model = root / "models" / "snapshot-abc"
    model.mkdir(parents=True)
    (model / "config.json").write_text('{"model":"test"}', encoding="utf-8")
    (model / "model.safetensors").write_bytes(b"weights")

    class Backend:
        backend_id = "voxcpm2"

        def discover_model(self, archive_root):
            del archive_root
            return model

    namespace = {
        "read_segments": read_segments,
        "prepare_reference": prepare_reference,
        "get_backend": lambda name: Backend(),
        "sha256_file": digest,
        "MAX_TEMPO": 1.36,
        "log": lambda message: events.append("log:" + str(message)),
    }

    direct_surgical_guard.install_guard_contract()
    direct_surgical_polish_v2.install_global_polish()
    original_preflight = guard.run_pre_model_guard

    def fake_preflight(segments, *, work_dir, max_tempo, signature_context):
        assert len(list(segments)) == 1
        assert Path(work_dir).name == "work"
        assert max_tempo == 1.36
        assert signature_context["final_audit_policy"] == final.POLICY
        events.append("preflight")
        return {"warning_ids": []}

    guard.run_pre_model_guard = fake_preflight
    try:
        final.install_final_audit(namespace)

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
        values = namespace["read_segments"](valid)
        assert values[0]["id"] == 1

        invalid_values = (
            {**base, "id": True},
            {**base, "id": 1.5},
            {**base, "start": True},
            {**base, "start_delay_ms": 1.5},
        )
        calls_before = len(reader_calls)
        for index, value in enumerate(invalid_values, 1):
            bad = write_payload(f"bad-{index}.json", [value])
            try:
                namespace["read_segments"](bad)
            except RuntimeError:
                pass
            else:
                raise AssertionError(f"raw invalid segment accepted: {value!r}")
        assert len(reader_calls) == calls_before

        source = root / "source.wav"
        source.write_bytes(b"reference")
        work = root / "work"
        first = work / "references_guarded" / "extended.wav"
        second = work / "references_guarded" / "composite.wav"
        namespace["prepare_reference"](source, first, None)
        namespace["prepare_reference"](source, second, None)
        assert events.index("preflight") < events.index("prepare")
        assert events.count("preflight") == 1
        assert events.count("prepare") == 2

        backend = namespace["get_backend"]("voxcpm2")
        assert backend.discover_model(root / "models") == model.resolve()
        context = guard.load_signature_context(work)
        assert context["final_audit_policy"] == final.POLICY
        assert context["segments_json_sha256"] == digest(valid)
        assert context["direct_model_snapshot"] == "snapshot-abc"
        assert context["direct_model_config_sha256"] == digest(model / "config.json")
        assert len(context["direct_model_snapshot_fingerprint"]) == 64

        try:
            retry.load_retry_epoch(root / "retry", 1.5)
        except RuntimeError:
            pass
        else:
            raise AssertionError("fractional retry segment_id was accepted")

        timing = work / "timing_blocks"
        timing.mkdir(parents=True, exist_ok=True)
        for index in range(12):
            (timing / f"segment_01.json.stale-test-{index:02d}").write_text(
                str(index), encoding="utf-8"
            )
        final._prune_timing_archives(work, 1)
        assert len(list(timing.glob("segment_01.json.stale-*"))) == final.MAX_ARCHIVED_MARKERS

        entrypoint = (
            Path.cwd() / "tools" / "voxcpm2" / "direct_max_quality_cli.py"
        ).read_text(encoding="utf-8")
        assert "install_final_audit(globals())" in entrypoint
    finally:
        guard.run_pre_model_guard = original_preflight

print("final direct audit contracts: 9 checks passed")
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
    assert "9 checks passed" in details
