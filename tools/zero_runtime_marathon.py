#!/usr/bin/env python3
"""Temporary branch-only diagnostics for zero-runtime title-policy migration."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEEDLES = (
    "install_dub_title_policy",
    "install_voxcpm_title_policy",
    "canonical_media_title",
    "canonical_delivery_filename",
    "sentence_case_russian_title",
    "def _row_project",
    "def _undelivered_notification_events",
    "def available_outputs",
    "def collect_dub_health",
    "def _russian_heading_case",
)


def main() -> int:
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if any(part in {".git", ".venv", "venv", "__pycache__"} for part in rel.parts):
            continue
        if rel.as_posix() == "tools/zero_runtime_marathon.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        matches = [(i + 1, line) for i, line in enumerate(lines) if any(n in line for n in NEEDLES)]
        if not matches:
            continue
        print(f"\n### {rel}")
        for lineno, line in matches:
            print(f"{lineno:05d}: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
