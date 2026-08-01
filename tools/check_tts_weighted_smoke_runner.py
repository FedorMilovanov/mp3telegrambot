#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate a trusted weighted-TTS runner without loading model weights."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from services.tts_weighted_smoke_runner import (
    WeightedTTSSmokeRunnerConfig,
    run_weighted_tts_runner_doctor,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate production profile resolution, model discovery, runtime "
            "imports, reference audio, ffprobe and atomic storage before weighted "
            "TTS synthesis."
        )
    )
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--reference-wav", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--expected-python")
    return parser


def _redacted_failure(exc: BaseException, config: WeightedTTSSmokeRunnerConfig) -> str:
    message = " ".join(str(exc or type(exc).__name__).split())
    replacements = {
        str(config.model_root): "<MODEL_ROOT>",
        str(config.reference_wav): "<REFERENCE_WAV>",
        str(config.work_dir): "<WORK_DIR>",
        str(config.expected_python or ""): "<EXPECTED_PYTHON>",
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
    config = WeightedTTSSmokeRunnerConfig(
        profile_id=args.profile_id,
        model_root=Path(args.model_root),
        reference_wav=Path(args.reference_wav),
        work_dir=Path(args.work_dir),
        expected_python=(Path(args.expected_python) if args.expected_python else None),
    )
    try:
        report = run_weighted_tts_runner_doctor(config)
    except Exception as exc:
        print(
            f"TTS_WEIGHTED_SMOKE_RUNNER_FAILED {type(exc).__name__}: "
            f"{_redacted_failure(exc, config)}",
            file=sys.stderr,
        )
        return 1

    profile = report.get("profile") or {}
    backend = report.get("backend") or {}
    imports = report.get("imports") or {}
    print(
        "TTS_WEIGHTED_SMOKE_RUNNER_OK "
        f"profile={profile.get('profile_id')} "
        f"revision={profile.get('model_revision')} "
        f"backend={backend.get('backend_id')} "
        f"modules={len(imports.get('modules') or [])} "
        "weights_loaded=false"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
