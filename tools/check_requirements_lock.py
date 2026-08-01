#!/usr/bin/env python3
"""Validate that direct requirements are covered by one exact resolved lock."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

POLICY = "exact-resolved-python-requirements-lock-v1"
DEFAULT_DIRECT_FILES = (Path("requirements.txt"), Path("requirements-dev.txt"))
DEFAULT_LOCK = Path("requirements-lock.txt")


def _requirement_lines(path: Path) -> Iterable[str]:
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        yield line


def direct_requirements(paths: Iterable[Path]) -> dict[str, Requirement]:
    result: dict[str, Requirement] = {}
    for path in paths:
        for line in _requirement_lines(Path(path)):
            requirement = Requirement(line)
            name = canonicalize_name(requirement.name)
            existing = result.get(name)
            if existing is not None and str(existing) != str(requirement):
                raise RuntimeError(
                    f"Conflicting direct requirements for {name}: "
                    f"{existing!s} vs {requirement!s}"
                )
            result[name] = requirement
    return result


def locked_requirements(path: Path) -> dict[str, Requirement]:
    result: dict[str, Requirement] = {}
    for line in _requirement_lines(Path(path)):
        requirement = Requirement(line)
        name = canonicalize_name(requirement.name)
        if name in result:
            raise RuntimeError(f"Duplicate locked package: {name}")
        if requirement.url is not None:
            raise RuntimeError(f"Lock entries must not use direct URLs: {line}")
        specifiers = list(requirement.specifier)
        if len(specifiers) != 1 or specifiers[0].operator != "==":
            raise RuntimeError(f"Lock entry must contain one exact == pin: {line}")
        if requirement.marker is not None:
            raise RuntimeError(f"Platform markers are not allowed in this lock: {line}")
        result[name] = requirement
    return result


def validate_lock(
    *,
    direct_paths: Iterable[Path] = DEFAULT_DIRECT_FILES,
    lock_path: Path = DEFAULT_LOCK,
) -> dict[str, object]:
    direct = direct_requirements(direct_paths)
    locked = locked_requirements(lock_path)
    missing = sorted(set(direct) - set(locked))
    if missing:
        raise RuntimeError(
            "Direct requirements missing from resolved lock: " + ", ".join(missing)
        )
    incompatible: list[str] = []
    for name, requirement in direct.items():
        locked_version = next(iter(locked[name].specifier)).version
        if requirement.specifier and not requirement.specifier.contains(
            locked_version,
            prereleases=True,
        ):
            incompatible.append(
                f"{name}: direct={requirement.specifier}, locked={locked_version}"
            )
    if incompatible:
        raise RuntimeError(
            "Locked versions violate direct constraints: " + "; ".join(incompatible)
        )
    return {
        "policy": POLICY,
        "direct_count": len(direct),
        "locked_count": len(locked),
        "lock_path": str(Path(lock_path)),
        "covered": sorted(direct),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument(
        "requirements",
        nargs="*",
        type=Path,
        default=list(DEFAULT_DIRECT_FILES),
    )
    args = parser.parse_args()
    result = validate_lock(
        direct_paths=args.requirements or DEFAULT_DIRECT_FILES,
        lock_path=args.lock,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"REQUIREMENTS_LOCK_FAILED: {type(exc).__name__}: {exc}")
        raise SystemExit(1) from exc
