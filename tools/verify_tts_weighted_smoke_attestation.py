#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify one downloaded weighted-TTS attestation artifact fail-closed."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from services.tts_weighted_smoke import _strict_json_object
from services.tts_weighted_smoke_attestation import (
    validate_weighted_tts_attestation,
)

_MAX_ATTESTATION_BYTES = 1_000_000
_EXPECTED_FILENAME = "attestation.json"
_OUTPUT_KEYS = (
    "attestation_digest",
    "commit_sha",
    "run_id",
    "run_attempt",
    "profile_id",
    "model_revision",
    "backend_id",
    "output_duration_seconds",
    "output_sample_rate",
    "audio_retained",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Require an artifact directory containing only attestation.json, "
            "validate its digest/privacy schema and bind it to exact GitHub run identity."
        )
    )
    parser.add_argument("--artifact-directory", required=True)
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-run-id", required=True, type=int)
    parser.add_argument("--expected-run-attempt", required=True, type=int)
    parser.add_argument("--expected-profile-id", required=True)
    parser.add_argument("--github-output")
    return parser


def _artifact_file(directory: Path) -> Path:
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError("Attestation artifact directory не найдена.")
    entries = tuple(root.iterdir())
    if len(entries) != 1:
        raise RuntimeError("Attestation artifact должен содержать ровно один entry.")
    path = entries[0]
    if path.is_symlink() or not path.is_file() or path.name != _EXPECTED_FILENAME:
        raise RuntimeError("Attestation artifact содержит неизвестный или небезопасный entry.")
    size = path.stat().st_size
    if not 1 <= size <= _MAX_ATTESTATION_BYTES:
        raise RuntimeError("Attestation JSON имеет недопустимый размер.")
    return path


def _load(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("Attestation JSON не читается как UTF-8.") from exc
    return _strict_json_object(text, label="weighted smoke attestation")


def _positive_int(value: int, label: str) -> int:
    if isinstance(value, bool) or int(value) <= 0:
        raise ValueError(f"{label} должен быть положительным int.")
    return int(value)


def _safe_scalar(value: object, label: str) -> str:
    text = str(value)
    if not text or len(text) > 256 or "\n" in text or "\r" in text or "=" in text:
        raise RuntimeError(f"Unsafe GitHub output scalar: {label}.")
    return text


def verify_downloaded_attestation(
    artifact_directory: Path,
    *,
    expected_repository: str,
    expected_commit: str,
    expected_run_id: int,
    expected_run_attempt: int,
    expected_profile_id: str,
) -> dict[str, str]:
    path = _artifact_file(artifact_directory)
    payload = _load(path)
    validate_weighted_tts_attestation(
        payload,
        forbidden_values=(str(Path(artifact_directory).resolve()), str(path.resolve())),
    )
    subject = payload["subject"]
    result = payload["result"]
    repository = str(expected_repository or "").strip()
    commit = str(expected_commit or "").strip().casefold()
    run_id = _positive_int(expected_run_id, "expected_run_id")
    run_attempt = _positive_int(expected_run_attempt, "expected_run_attempt")
    profile_id = str(expected_profile_id or "").strip()
    if subject.get("repository") != repository:
        raise ValueError("Attestation repository не совпадает с workflow repository.")
    if subject.get("commit_sha") != commit:
        raise ValueError("Attestation commit не совпадает с workflow commit.")
    if subject.get("run_id") != run_id or subject.get("run_attempt") != run_attempt:
        raise ValueError("Attestation run identity не совпадает с workflow run.")
    if result.get("profile_id") != profile_id:
        raise ValueError("Attestation profile не совпадает с dispatch profile.")
    if result.get("passed") is not True or result.get("audio_retained") is not False:
        raise ValueError("Attestation не подтверждает passed/audio cleanup invariants.")
    outputs = {
        "attestation_digest": str(payload["digest_sha256"]),
        "commit_sha": str(subject["commit_sha"]),
        "run_id": str(subject["run_id"]),
        "run_attempt": str(subject["run_attempt"]),
        "profile_id": str(result["profile_id"]),
        "model_revision": str(result["model_revision"]),
        "backend_id": str(result["backend_id"]),
        "output_duration_seconds": str(result["output_duration_seconds"]),
        "output_sample_rate": str(result["output_sample_rate"]),
        "audio_retained": "false",
    }
    if set(outputs) != set(_OUTPUT_KEYS):
        raise AssertionError("Verifier output contract drifted.")
    return {
        key: _safe_scalar(outputs[key], key)
        for key in _OUTPUT_KEYS
    }


def _append_github_outputs(path: Path, outputs: dict[str, str]) -> None:
    destination = Path(path).expanduser().resolve()
    if not destination.parent.is_dir():
        raise RuntimeError("GITHUB_OUTPUT parent directory не существует.")
    with destination.open("a", encoding="utf-8", newline="\n") as handle:
        for key in _OUTPUT_KEYS:
            handle.write(f"{key}={outputs[key]}\n")
        handle.flush()
        os.fsync(handle.fileno())


def _redacted_failure(exc: BaseException, artifact_directory: Path) -> str:
    message = " ".join(str(exc or type(exc).__name__).split())
    replacements = {
        str(Path(artifact_directory).expanduser().resolve()): "<ARTIFACT_DIRECTORY>",
        str(Path.cwd().resolve()): "<REPOSITORY>",
        str(Path(sys.executable).resolve()): "<PYTHON>",
    }
    for raw, marker in sorted(
        replacements.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if raw:
            message = message.replace(raw, marker)
            message = message.replace(raw.replace("\\", "/"), marker)
    return message[:2000] or type(exc).__name__


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    artifact_directory = Path(args.artifact_directory)
    try:
        outputs = verify_downloaded_attestation(
            artifact_directory,
            expected_repository=args.expected_repository,
            expected_commit=args.expected_commit,
            expected_run_id=args.expected_run_id,
            expected_run_attempt=args.expected_run_attempt,
            expected_profile_id=args.expected_profile_id,
        )
        if args.github_output:
            _append_github_outputs(Path(args.github_output), outputs)
    except Exception as exc:
        print(
            "TTS_WEIGHTED_SMOKE_ATTESTATION_VERIFY_FAILED "
            f"{type(exc).__name__}: {_redacted_failure(exc, artifact_directory)}",
            file=sys.stderr,
        )
        return 1
    print(
        "TTS_WEIGHTED_SMOKE_ATTESTATION_VERIFIED "
        f"digest={outputs['attestation_digest']} "
        f"commit={outputs['commit_sha']} "
        f"run={outputs['run_id']}.{outputs['run_attempt']} "
        f"profile={outputs['profile_id']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
