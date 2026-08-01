#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate an already registered weighted-TTS Windows self-hosted runner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from services.tts_weighted_runner_provisioning import (
    WeightedTTSRunnerProvisioningConfig,
    run_weighted_tts_runner_provisioning_check,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a persistent Windows GitHub Actions runner, its machine "
            "environment and the no-weights TTS doctor. No registration token "
            "is accepted or used."
        )
    )
    parser.add_argument("--runner-directory", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--profile-id", default="voxcpm2-production-v1")
    parser.add_argument("--python-executable", required=True)
    parser.add_argument("--model-directory", required=True)
    parser.add_argument("--reference-wav", required=True)
    parser.add_argument("--work-directory", required=True)
    return parser


def _redacted_failure(
    exc: BaseException,
    config: WeightedTTSRunnerProvisioningConfig,
) -> str:
    message = " ".join(str(exc or type(exc).__name__).split())
    replacements = {
        str(config.runner_directory): "<RUNNER_DIRECTORY>",
        str(config.python_executable): "<PYTHON_EXECUTABLE>",
        str(config.model_directory): "<MODEL_DIRECTORY>",
        str(config.reference_wav): "<REFERENCE_WAV>",
        str(config.work_directory): "<WORK_DIRECTORY>",
        str(Path(sys.executable).resolve()): "<CURRENT_PYTHON>",
        str(Path.cwd().resolve()): "<REPOSITORY_DIRECTORY>",
    }
    for raw, marker in sorted(
        replacements.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if not raw:
            continue
        message = message.replace(raw, marker)
        message = message.replace(raw.replace("\\", "/"), marker)
    return message[:2000] or type(exc).__name__


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = WeightedTTSRunnerProvisioningConfig(
        runner_directory=Path(args.runner_directory),
        repository=args.repository,
        profile_id=args.profile_id,
        python_executable=Path(args.python_executable),
        model_directory=Path(args.model_directory),
        reference_wav=Path(args.reference_wav),
        work_directory=Path(args.work_directory),
    )
    try:
        report = run_weighted_tts_runner_provisioning_check(config)
    except Exception as exc:
        print(
            "TTS_WEIGHTED_RUNNER_PROVISIONING_FAILED "
            f"{type(exc).__name__}: {_redacted_failure(exc, config)}",
            file=sys.stderr,
        )
        return 1

    report_path = config.work_directory / "setup-report.json"
    json.loads(report_path.read_text(encoding="utf-8"))
    doctor = report.get("doctor") or {}
    runner = report.get("runner") or {}
    print(
        "TTS_WEIGHTED_RUNNER_PROVISIONING_OK "
        f"profile={doctor.get('profile_id')} "
        f"backend={doctor.get('backend_id')} "
        f"service_running={runner.get('service_running')} "
        f"weights_loaded={doctor.get('weights_loaded')}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
