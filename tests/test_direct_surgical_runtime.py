from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.voxcpm2 import direct_surgical_guard
from tools.voxcpm2 import direct_surgical_runtime as runtime
from tools.voxcpm2 import direct_timing_guard as guard


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_repo(root: Path, marker: str = "") -> Path:
    paths = runtime._RUNTIME_SCOPE_FILES
    for relative in paths:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(relative + marker, encoding="utf-8")
    return root


def namespace(repo: Path, scopes: list[str] | None = None):
    logs = []
    scopes = scopes if scopes is not None else []

    def read_segments(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def prepare(source, output, sf):
        del source, sf
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_bytes(b"prepared")
        return {
            "sha256": digest(Path(output)),
            "sample_rate": 16000,
            "duration": 8.0,
            "voiced_ratio": 0.5,
            "active_ratio": 0.8,
            "max_internal_gap": 0.1,
            "clipping_ratio": 0.0,
            "spectral_envelope": {"frames": 10, "bands": [0.1]},
        }

    return {
        "__file__": str(repo / "tools/voxcpm2/direct_max_quality_cli.py"),
        "read_segments": read_segments,
        "_build_generation_length_request": lambda segment, **kwargs: kwargs,
        "_acceptable_candidates": lambda candidates, slot: [],
        "_raw_failure_evidence": lambda candidates, **kwargs: {"attempts": candidates},
        "get_backend": lambda name: SimpleNamespace(backend_id="other"),
        "prepare_reference": prepare,
        "sha256_file": digest,
        "MAX_TEMPO": 1.36,
        "EXPECTED_ENCODE_SR": 16000,
        "EXPECTED_OUTPUT_SR": 48000,
        "log": logs.append,
        "logs": logs,
    }


def write_segments(path: Path) -> dict:
    item = {
        "id": 1,
        "text": "Короткая естественная фраза.",
        "start": 0.0,
        "end": 5.0,
        "tail_guard": 0.18,
        "speech_slot": 4.82,
        "reference_profile": "extended",
    }
    path.write_text(json.dumps([item]), encoding="utf-8")
    return item


def test_adaptive_budget_is_structured(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    ns = namespace(repo)
    runtime.install_surgical_runtime(ns)
    segments = repo / "segments.json"
    write_segments(segments)
    item = ns["read_segments"](segments)[0]
    ns["load_retry_epoch"](repo / "work", 1)
    ns["_build_generation_length_request"](
        item, duration_budget=4.82, attempt=1, previous_output_durations=()
    )
    candidates = [
        {
            "attempt": index,
            "seed": 100 + index,
            "duration": 4.5,
            "required_tempo": 4.5 / 4.82,
            "score": 150.0,
            "cadence_evidence": {"failures": ["quality"]},
            "tail_info": {"suspicious": False},
        }
        for index in (1, 2, 3)
    ]
    with pytest.raises(guard.RetryableSynthesisFailure) as caught:
        ns["_acceptable_candidates"](candidates, 4.82)
    assert caught.value.advance_retry is True
    assert caught.value.failure_kind == "adaptive_budget_exhausted"


def test_unchanged_marker_blocks_without_consuming_retry(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    ns = namespace(repo)
    runtime.install_surgical_runtime(ns)
    segments = repo / "segments.json"
    write_segments(segments)
    item = ns["read_segments"](segments)[0]
    work = repo / "work"
    guarded = work / "references_guarded"
    guarded.mkdir(parents=True)
    (guarded / "extended.wav").write_bytes(b"reference")
    ns["load_retry_epoch"](work, 1)
    state = ns["_SURGICAL_RUNTIME_STATE"]
    context = {
        **guard.load_signature_context(work),
        "surgical_runtime_policy": runtime.POLICY,
        "surgical_runtime_sha256": state["runtime_context"]["surgical_runtime_sha256"],
        "reference_profile": "extended",
        "reference_sha256": digest(guarded / "extended.wav"),
    }
    guard.persist_timing_block(
        work,
        segment=item,
        signature_context=context,
        retry_epoch=0,
        evidence={"kind": "test", "attempts": [], "max_tempo": 1.36},
    )
    with pytest.raises(guard.RetryableSynthesisFailure) as caught:
        ns["_build_generation_length_request"](
            item, duration_budget=4.82, attempt=1, previous_output_durations=()
        )
    assert caught.value.advance_retry is False


def test_runtime_code_changes_retry_scope(tmp_path: Path) -> None:
    values = []
    for suffix in ("a", "b"):
        repo = make_repo(tmp_path / suffix, suffix)
        ns = namespace(repo)
        runtime.install_surgical_runtime(ns)
        segments = repo / "segments.json"
        write_segments(segments)
        ns["read_segments"](segments)
        ns["load_retry_epoch"](repo / "work", 1)
        values.append(
            ns["_SURGICAL_RUNTIME_STATE"]["runtime_context"][
                "surgical_runtime_sha256"
            ]
        )
    assert values[0] != values[1]


def test_unrelated_acceptable_error_is_not_reclassified(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    ns = namespace(repo)

    def fail_unrelated(candidates, slot):
        del candidates, slot
        raise RuntimeError("unexpected analysis failure")

    ns["_acceptable_candidates"] = fail_unrelated
    runtime.install_surgical_runtime(ns)
    segments = repo / "segments.json"
    write_segments(segments)
    item = ns["read_segments"](segments)[0]
    ns["load_retry_epoch"](repo / "work", 1)
    ns["_build_generation_length_request"](
        item, duration_budget=4.82, attempt=1, previous_output_durations=()
    )
    with pytest.raises(RuntimeError, match="unexpected analysis failure"):
        ns["_acceptable_candidates"]([], 4.82)
