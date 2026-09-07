#!/usr/bin/env python3
"""Fail-closed direct-launch verification against the repository dependency lock.

The managed BAT launcher installs the full resolved lock. Direct
``python bot_new.py`` launches cannot assume that happened, so this module
reuses the repository's canonical requirements contract and verifies the
installed runtime dependency closure against exact lock pins. Dev-only packages
remain outside that closure unless a runtime package actually requires them.
"""
from __future__ import annotations

from collections import deque
from importlib import metadata
from pathlib import Path
from typing import Iterable

POLICY = "direct-runtime-dependency-lock-v2"


class RuntimeDependencyLockError(RuntimeError):
    """Raised when the direct runtime environment is not lock-compatible."""


def _requirement_tools():
    """Load the canonical repository requirement parser lazily and fail closed."""
    try:
        from packaging.requirements import Requirement
        from packaging.utils import canonicalize_name
        from packaging.version import InvalidVersion, Version
        from tools.check_requirements_lock import (
            direct_requirements,
            locked_requirements,
            validate_lock,
        )
    except ImportError as exc:
        missing = getattr(exc, "name", None) or type(exc).__name__
        raise RuntimeDependencyLockError(
            "Dependency-lock verifier is unavailable "
            f"(import failure: {missing}); install requirements-lock.txt"
        ) from exc

    return (
        Requirement,
        canonicalize_name,
        Version,
        InvalidVersion,
        direct_requirements,
        locked_requirements,
        validate_lock,
    )


def _marker_applies(requirement, active_extras: set[str]) -> bool:
    marker = requirement.marker
    if marker is None:
        return True
    contexts = [""]
    contexts.extend(sorted(active_extras))
    return any(marker.evaluate({"extra": extra}) for extra in contexts)


def validate_runtime_dependency_lock(
    project_root: Path,
    *,
    skip_names: Iterable[str] = (),
) -> dict[str, object]:
    """Verify repository lock validity and exact installed runtime closure pins."""
    root = Path(project_root)
    direct_path = root / "requirements.txt"
    lock_path = root / "requirements-lock.txt"
    if not direct_path.is_file():
        raise RuntimeDependencyLockError("requirements.txt is missing")
    if not lock_path.is_file():
        raise RuntimeDependencyLockError("requirements-lock.txt is missing")

    (
        Requirement,
        canonicalize_name,
        Version,
        InvalidVersion,
        direct_requirements,
        locked_requirements,
        validate_lock,
    ) = _requirement_tools()

    try:
        lock_result = validate_lock(
            direct_paths=(direct_path,),
            lock_path=lock_path,
        )
        direct = direct_requirements((direct_path,))
        locked = locked_requirements(lock_path)
    except RuntimeDependencyLockError:
        raise
    except Exception as exc:
        raise RuntimeDependencyLockError(
            "Repository dependency lock is invalid: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    skipped = {canonicalize_name(name) for name in skip_names}
    active_extras: dict[str, set[str]] = {}
    queue: deque[str] = deque()
    skipped_count = 0
    platform_skipped = 0

    for name, requirement in direct.items():
        extras = set(requirement.extras)
        try:
            applies = _marker_applies(requirement, extras)
        except Exception as exc:
            raise RuntimeDependencyLockError(
                f"Direct runtime requirement marker is invalid for {name}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if not applies:
            platform_skipped += 1
            continue
        if name in skipped:
            skipped_count += 1
            continue
        active_extras[name] = extras
        queue.append(name)

    mismatches: list[str] = []
    checked_versions: set[str] = set()
    expanded_extras: dict[str, frozenset[str]] = {}

    while queue:
        name = queue.popleft()
        extras = active_extras.setdefault(name, set())
        previous_extras = expanded_extras.get(name)
        current_extras = frozenset(extras)

        locked_requirement = locked.get(name)
        if locked_requirement is None:
            mismatches.append(f"{name}=missing-from-lock")
            continue
        expected = next(iter(locked_requirement.specifier)).version

        if name not in checked_versions:
            try:
                installed = metadata.version(name)
            except metadata.PackageNotFoundError:
                mismatches.append(f"{name}=missing (locked {expected})")
                continue
            except Exception as exc:
                mismatches.append(
                    f"{name}=unreadable:{type(exc).__name__} (locked {expected})"
                )
                continue

            try:
                matches = Version(installed) == Version(expected)
            except InvalidVersion:
                matches = installed == expected
            if not matches:
                mismatches.append(f"{name}={installed} (locked {expected})")
                continue
            checked_versions.add(name)

        if previous_extras == current_extras:
            continue
        expanded_extras[name] = current_extras

        try:
            requires = metadata.requires(name) or ()
        except metadata.PackageNotFoundError:
            mismatches.append(f"{name}=metadata-missing (locked {expected})")
            continue
        except Exception as exc:
            mismatches.append(
                f"{name}=requires-unreadable:{type(exc).__name__} (locked {expected})"
            )
            continue

        for raw_dependency in requires:
            try:
                dependency = Requirement(raw_dependency)
                applies = _marker_applies(dependency, extras)
            except Exception as exc:
                mismatches.append(
                    f"{name}=invalid-requires:{type(exc).__name__}"
                )
                continue
            if not applies:
                continue

            dependency_name = canonicalize_name(dependency.name)
            dependency_lock = locked.get(dependency_name)
            if dependency_lock is None:
                mismatches.append(
                    f"{dependency_name}=required-by-{name}-but-missing-from-lock"
                )
                continue
            dependency_expected = next(iter(dependency_lock.specifier)).version
            if dependency.specifier and not dependency.specifier.contains(
                dependency_expected,
                prereleases=True,
            ):
                mismatches.append(
                    f"{dependency_name}=locked-{dependency_expected}-violates-"
                    f"{name}-requirement-{dependency.specifier}"
                )
                continue

            existing_extras = active_extras.setdefault(dependency_name, set())
            before = frozenset(existing_extras)
            existing_extras.update(dependency.extras)
            if dependency_name not in checked_versions or before != frozenset(existing_extras):
                queue.append(dependency_name)

    if mismatches:
        unique_mismatches = list(dict.fromkeys(mismatches))
        preview = "; ".join(unique_mismatches[:8])
        if len(unique_mismatches) > 8:
            preview += f"; +{len(unique_mismatches) - 8} more"
        raise RuntimeDependencyLockError(
            "Installed runtime dependency closure differs from requirements-lock.txt: "
            + preview
        )

    return {
        "policy": POLICY,
        "lock_policy": lock_result.get("policy"),
        "checked": len(checked_versions),
        "direct": len(direct),
        "skipped": skipped_count,
        "platform_skipped": platform_skipped,
        "runtime_closure": len(active_extras),
    }


__all__ = [
    "POLICY",
    "RuntimeDependencyLockError",
    "validate_runtime_dependency_lock",
]
