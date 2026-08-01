#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build one privacy-safe weighted TTS GitHub Actions attestation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from services.tts_weighted_smoke_attestation import (
    WeightedTTSSmokeAttestationContext,
    build_weighted_tts_attestation,
    load_weighted_tts_report,
    validate_weighted_tts_attestation,
    write_weighted_tts_attestation,
)


def _environment(name: str) -> str:
    return str(os.getenv(name, "")).strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Cross-check runner-doctor and real synthesis reports, bind the result "
            "to immutable GitHub Actions identity and write one sanitized attestation."
        )
    )
    parser.add_argument("--doctor-report", required=True)
    parser.add_argument("--smoke-report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repository", default=_environment("GITHUB_REPOSITORY"))
    parser.add_argument("--commit-sha", default=_environment("GITHUB_SHA"))
    parser.add_argument("--ref", default=_environment("GITHUB_REF"))
    parser.add_argument("--event-name", default=_environment("GITHUB_EVENT_NAME"))
    parser.add_argument("--workflow-ref", default=_environment("GITHUB_WORKFLOW_REF"))
    parser.add_argument("--run-id", default=_environment("GITHUB_RUN_ID"))
    parser.add_argument("--run-attempt", default=_environment("GITHUB_RUN_ATTEMPT"))
    return parser


def _redacted_failure(
    exc: BaseException,
    *,
    doctor_report: Path,
    smoke_report: Path,
    output: Path,
) -> str:
    message = " ".join(str(exc or type(exc).__name__).split())
    replacements = {
        str(doctor_report.resolve()): "<DOCTOR_REPORT>",
        str(smoke_report.resolve()): "<SMOKE_REPORT>",
        str(output.resolve()): "<ATTESTATION>",
        str(Path(sys.executable).resolve()): "<CURRENT_PYTHON>",
        str(Path.cwd().resolve()): "<REPOSITORY>",
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
    doctor_path = Path(args.doctor_report).expanduser().resolve()
    smoke_path = Path(args.smoke_report).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    forbidden_values = (
        str(doctor_path),
        str(smoke_path),
        str(output_path),
        str(Path(sys.executable).resolve()),
        str(Path.cwd().resolve()),
    )
    try:
        context = WeightedTTSSmokeAttestationContext(
            repository=args.repository,
            commit_sha=args.commit_sha,
            ref=args.ref,
            event_name=args.event_name,
            workflow_ref=args.workflow_ref,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
        )
        doctor = load_weighted_tts_report(doctor_path, label="runner doctor")
        smoke = load_weighted_tts_report(smoke_path, label="weighted smoke")
        attestation = build_weighted_tts_attestation(
            doctor,
            smoke,
            context,
            forbidden_values=forbidden_values,
        )
        write_weighted_tts_attestation(output_path, attestation)
        retained = json.loads(output_path.read_text(encoding="utf-8"))
        validate_weighted_tts_attestation(
            retained,
            forbidden_values=forbidden_values,
        )
    except Exception as exc:
        print(
            "TTS_WEIGHTED_SMOKE_ATTESTATION_FAILED "
            f"{type(exc).__name__}: "
            f"{_redacted_failure(exc, doctor_report=doctor_path, smoke_report=smoke_path, output=output_path)}",
            file=sys.stderr,
        )
        return 1

    result = attestation["result"]
    subject = attestation["subject"]
    print(
        "TTS_WEIGHTED_SMOKE_ATTESTATION_OK "
        f"commit={subject['commit_sha']} "
        f"run={subject['run_id']}.{subject['run_attempt']} "
        f"profile={result['profile_id']} "
        f"revision={result['model_revision']} "
        f"digest={attestation['digest_sha256']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
