#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run one privacy-safe real-weight TTS smoke on a trusted self-hosted runner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from services.tts_weighted_smoke import (
    WeightedTTSSmokeConfig,
    run_weighted_tts_smoke,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Load the selected production TTS profile, perform one real synthesis, "
            "validate mono PCM/WAV/FFprobe and retain only a sanitized JSON report."
        )
    )
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--model-root", required=True)
    parser.add_argument("--reference-wav", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--expected-python")
    parser.add_argument(
        "--text",
        default="Это короткая проверка настоящего синтеза русской речи.",
    )
    parser.add_argument("--duration-budget", type=float, default=4.0)
    parser.add_argument("--seed", type=int, default=2026080101)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = WeightedTTSSmokeConfig(
        profile_id=args.profile_id,
        model_root=Path(args.model_root),
        reference_wav=Path(args.reference_wav),
        work_dir=Path(args.work_dir),
        expected_python=(Path(args.expected_python) if args.expected_python else None),
        text=args.text,
        duration_budget=args.duration_budget,
        seed=args.seed,
    )
    report = run_weighted_tts_smoke(config)
    output = report.get("output") or {}
    pcm = output.get("pcm") or {}
    profile = report.get("profile") or {}
    backend = report.get("backend") or {}
    print(
        "TTS_WEIGHTED_SMOKE_OK "
        f"profile={profile.get('profile_id')} "
        f"revision={profile.get('model_revision')} "
        f"backend={backend.get('backend_id')} "
        f"duration={float(pcm.get('duration_seconds') or 0.0):.3f}s "
        f"sample_rate={int(pcm.get('sample_rate') or 0)}"
    )
    # Prove the retained report is valid JSON without printing its contents.
    report_path = config.work_dir / "report.json"
    json.loads(report_path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        print(
            f"TTS_WEIGHTED_SMOKE_FAILED {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
