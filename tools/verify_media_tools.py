#!/usr/bin/env python3
"""Fail closed unless approved FFmpeg/FFprobe tools can be identified."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from services.process_executables import resolve_required_executable


VERSION_RE = re.compile(r"^ff(?:mpeg|probe) version (?P<version>\S+)")


class MediaToolError(RuntimeError):
    """An executable is absent, unapproved, or cannot be inspected."""


@dataclass(frozen=True)
class MediaToolIdentity:
    name: str
    path: str
    version: str
    sha256: str


def _resolve(name: str) -> str:
    try:
        return resolve_required_executable(name)
    except FileNotFoundError as exc:
        raise MediaToolError(str(exc)) from exc


def _query_version(executable: str) -> str:
    try:
        completed = subprocess.run(
            [executable, "-version"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MediaToolError(f"cannot execute {executable}: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        raise MediaToolError(f"{executable} -version failed: {detail}")
    first_line = completed.stdout.splitlines()[0] if completed.stdout.splitlines() else ""
    match = VERSION_RE.match(first_line)
    if match is None:
        raise MediaToolError(f"unrecognized version output from {executable}: {first_line!r}")
    return match.group("version")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_media_tools(expected_version: str) -> tuple[MediaToolIdentity, MediaToolIdentity]:
    if not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", expected_version):
        raise MediaToolError(f"invalid expected media version: {expected_version!r}")

    identities: list[MediaToolIdentity] = []
    for name in ("ffmpeg", "ffprobe"):
        executable = _resolve(name)
        version = _query_version(executable)
        if version != expected_version and not version.startswith(f"{expected_version}-"):
            raise MediaToolError(f"{name} version {version!r} is not approved {expected_version!r}")
        identities.append(MediaToolIdentity(name, executable, version, _sha256(executable)))

    if identities[0].version != identities[1].version:
        raise MediaToolError(f"FFmpeg/FFprobe version mismatch: {identities[0].version} != {identities[1].version}")
    return identities[0], identities[1]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-version", required=True, help="Approved upstream FFmpeg version")
    args = parser.parse_args(argv)
    try:
        identities = inspect_media_tools(args.expected_version)
    except (MediaToolError, OSError) as exc:
        print(f"media tool verification failed: {exc}", file=sys.stderr)
        return 1
    for identity in identities:
        print(f"{identity.name}: path={identity.path} version={identity.version} sha256={identity.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
