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
from types import SimpleNamespace
import tempfile

from tools.voxcpm2 import direct_retry_epoch as retry
from tools.voxcpm2 import direct_surgical_guard
from tools.voxcpm2 import direct_surgical_io as surgical_io
from tools.voxcpm2 import direct_surgical_runtime as surgical_runtime
from tools.voxcpm2 import direct_timing_guard as guard
from tools.voxcpm2 import direct_surgical_polish_v2 as polish


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def segment(**extra):
    value = {
        "id": 1,
        "text": "Короткая естественная фраза.",
        "start": 0.0,
        "end": 4.0,
        "tail_guard": 0.18,
    }
    value.update(extra)
    return value


direct_surgical_guard.install_guard_contract()
polish.install_global_polish()

with tempfile.TemporaryDirectory() as raw:
    root = Path(raw)

    for bad in (True, 1.5):
        try:
            guard.run_pre_model_guard(
                [segment(id=bad)],
                work_dir=root / "preflight",
                max_tempo=1.36,
                signature_context={},
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError(f"invalid segment id accepted: {bad!r}")

    item = segment()
    work = root / "marker"
    marker = guard.persist_timing_block(
        work,
        segment=item,
        signature_context={},
        retry_epoch=0,
        evidence={"attempts": [], "max_tempo": 1.36},
    )
    assert marker["schema_version"] == polish.MARKER_SCHEMA_VERSION
    assert guard.load_matching_timing_block(
        work, segment=item, signature_context={}
    ) is not None
    marker_path = work / "timing_blocks" / "segment_01.json"
    payload = json.loads(marker_path.read_text(encoding="utf-8"))
    payload["speech_slot"] = 99.0
    marker_path.write_text(json.dumps(payload), encoding="utf-8")
    assert guard.load_matching_timing_block(
        work, segment=item, signature_context={}
    ) is None
    assert list((work / "timing_blocks").glob("*.stale-contract-mismatch-*"))

    runtime_work = root / "runtime"
    runtime_work.mkdir()
    (runtime_work / "direct_cli_runtime.marker.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "policy": "direct-cli-runtime-marker-v2",
                "speech_backend": "voxcpm2",
                "render_contract_sha256": "a" * 64,
                "cache_length": 4096,
                "python_executable": "python",
            }
        ),
        encoding="utf-8",
    )
    context = guard.load_signature_context(runtime_work)
    assert context["render_contract_sha256"] == "a" * 64
    assert context["runtime_marker_policy"] == "direct-cli-runtime-marker-v2"
    (runtime_work / "direct_cli_runtime.marker.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "policy": "direct-cli-runtime-marker-v2",
                "speech_backend": "voxcpm2",
                "render_contract_sha256": "a" * 64,
                "cache_length": True,
                "python_executable": "python",
            }
        ),
        encoding="utf-8",
    )
    context = guard.load_signature_context(runtime_work)
    assert context["runtime_marker_policy"] == "missing-or-invalid-direct-runtime-marker"
    assert "render_contract_sha256" not in context

    scope = "b" * 64
    assert retry._scope_epochs(
        {
            "scope_epochs": {scope: 0},
            "history": [
                {
                    "scope_epoch_to": 1,
                    "evidence": {"failure_scope_fingerprint": scope},
                },
                {
                    "scope_epoch_to": 2,
                    "evidence": {"failure_scope_fingerprint": scope},
                },
            ],
        }
    )[scope] == 2

    retry_work = root / "retry"
    first, second = "c" * 64, "d" * 64
    retry.invalidate_segment_for_retry(
        retry_work,
        {"id": 1},
        reason="raw_candidate_hard_failure",
        evidence={"failure_scope_fingerprint": first},
    )
    result = retry.invalidate_segment_for_retry(
        retry_work,
        {"id": 1},
        reason="raw_candidate_hard_failure",
        evidence={"failure_scope_fingerprint": second},
    )
    assert result["raw_retry_epoch"] == 2
    assert result["retry_epoch"] == 1
    assert result["scope_retry_epoch"] == 1
    assert result["last_scope_epoch"] == 1

    class Backend:
        def capabilities(self):
            raise RuntimeError("broken capability probe")

        def open_session(self, config):
            del config
            return SimpleNamespace(
                audio_spec=SimpleNamespace(
                    encode_sample_rate=16000,
                    output_sample_rate=48000,
                    seconds_per_step=0.08,
                    cache_length=4096,
                ),
                generate=lambda request: request,
            )

    session = surgical_io.LazySession(
        Backend(),
        SimpleNamespace(options={"cache_length": 4096}),
        encode=16000,
        output=48000,
        log=lambda message: None,
    )
    assert session.supports_continuation_context is False
    assert session.generate("audio") == "audio"

    source = root / "source.wav"
    output = root / "guarded" / "extended.wav"
    output.parent.mkdir()
    source.write_bytes(b"source")
    output.write_bytes(b"output")
    report = surgical_io.enrich_reference_report(
        {
            "sha256": digest(output),
            "sample_rate": 16000,
            "duration": 8.0,
            "voiced_ratio": 0.5,
            "active_ratio": 0.8,
            "max_internal_gap": 0.1,
            "clipping_ratio": 0.0,
            "spectral_envelope": {"frames": 10, "bands": [0.1, 0.2]},
        },
        source=source,
        hash_file=digest,
    )
    (output.parent / "references.json").write_text(
        json.dumps({"extended": report}), encoding="utf-8"
    )
    cached = surgical_io.cached_reference(
        source=source,
        output=output,
        hash_file=digest,
        expected_sample_rate=16000,
    )
    assert cached is not None and cached["reference_cache_hit"] is True
    legacy = dict(report)
    legacy.pop("reference_cache_schema_version")
    (output.parent / "references.json").write_text(
        json.dumps({"extended": legacy}), encoding="utf-8"
    )
    assert surgical_io.cached_reference(
        source=source,
        output=output,
        hash_file=digest,
        expected_sample_rate=16000,
    ) is None

    for relative in (
        "tools/voxcpm2/_direct_max_quality_cli_base.py",
        "tools/voxcpm2/direct_max_quality_analysis.py",
        "tools/voxcpm2/direct_timeline_delivery_qa.py",
        "services/speech_backends/execution_plan.py",
    ):
        assert relative in surgical_runtime._RUNTIME_SCOPE_FILES

    try:
        surgical_runtime._segments_by_id([segment(), segment()])
    except RuntimeError:
        pass
    else:
        raise AssertionError("duplicate segment ids were accepted")

print("second-pass surgical contracts: 10 checks passed")
'''


def test_second_pass_contracts_in_isolated_process() -> None:
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
    assert "10 checks passed" in details
