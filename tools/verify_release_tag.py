#!/usr/bin/env python3
"""Bind a release checkout to the exact local and remote commit of its tag."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence


TAG_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")


class ReleaseTagError(ValueError):
    """The requested tag cannot prove the identity of the checked-out source."""


def _git(*args: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise ReleaseTagError("git executable was not found on PATH")
    completed = subprocess.run(
        [executable, *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"git exited with {completed.returncode}"
        raise ReleaseTagError(detail)
    return completed.stdout.strip()


def _remote_tag_commits(tag: str) -> dict[str, str]:
    output = _git(
        "ls-remote",
        "--tags",
        "origin",
        f"refs/tags/{tag}",
        f"refs/tags/{tag}^{{}}",
    )
    entries: dict[str, str] = {}
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 2:
            raise ReleaseTagError(f"unexpected git ls-remote row: {line!r}")
        object_id, ref = fields
        if not COMMIT_RE.fullmatch(object_id):
            raise ReleaseTagError(f"unexpected remote object id for {ref}: {object_id!r}")
        if ref in entries:
            raise ReleaseTagError(f"duplicate remote ref: {ref}")
        entries[ref] = object_id
    return entries


def resolve_tag_commit(tag: str) -> str:
    """Return HEAD only when it is the commit peeled from the same remote tag."""

    if not TAG_RE.fullmatch(tag):
        raise ReleaseTagError(f"invalid release tag: {tag!r}")

    head = _git("rev-parse", "HEAD^{commit}")
    local_tag_commit = _git("rev-parse", f"refs/tags/{tag}^{{commit}}")
    remote_entries = _remote_tag_commits(tag)
    tag_ref = f"refs/tags/{tag}"
    peeled_ref = f"{tag_ref}^{{}}"
    if tag_ref not in remote_entries:
        raise ReleaseTagError(f"remote origin does not contain {tag_ref}")
    remote_tag_commit = remote_entries.get(peeled_ref, remote_entries[tag_ref])

    if not (head == local_tag_commit == remote_tag_commit):
        raise ReleaseTagError(
            f"release source mismatch for {tag}: HEAD={head}, "
            f"local tag={local_tag_commit}, remote peeled tag={remote_tag_commit}"
        )
    return head


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Exact vX.Y.Z tag requested for this release")
    args = parser.parse_args(argv)
    try:
        commit = resolve_tag_commit(args.tag)
    except ReleaseTagError as exc:
        print(f"release tag verification failed: {exc}", file=sys.stderr)
        return 1

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"commit_sha={commit}\n")
            handle.write(f"tag={args.tag}\n")
    print(f"verified release tag {args.tag} at {commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
