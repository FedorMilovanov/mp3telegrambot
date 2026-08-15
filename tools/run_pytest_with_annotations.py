#!/usr/bin/env python3
"""Run pytest and emit GitHub Actions annotations for failed node ids."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_FAILED_RE = re.compile(r"^FAILED\s+([^\s]+?)(?:\s+-\s+.*)?$", re.MULTILINE)


def _escape(value: str) -> str:
    return (
        str(value)
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _annotation(nodeid: str) -> str:
    path = nodeid.split("::", 1)[0]
    name = nodeid.split("::", 1)[1] if "::" in nodeid else nodeid
    return f"::error file={_escape(path)},title=pytest failure::{_escape(name)}"


def main(argv: list[str]) -> int:
    args = argv or ["-q"]
    command = [sys.executable, "-m", "pytest", *args]
    process = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=os.environ.copy(),
    )
    output = process.stdout or ""
    sys.stdout.write(output)
    if output and not output.endswith("\n"):
        sys.stdout.write("\n")

    if process.returncode:
        failed = list(dict.fromkeys(_FAILED_RE.findall(output)))
        for nodeid in failed:
            print(_annotation(nodeid))
        if not failed:
            targets = [arg for arg in args if arg.endswith(".py")]
            target = targets[0] if targets else "pytest"
            print(
                f"::error file={_escape(target)},title=pytest failure::"
                "pytest exited non-zero; inspect the captured output"
            )
    return int(process.returncode)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
