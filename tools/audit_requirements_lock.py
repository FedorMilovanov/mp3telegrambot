#!/usr/bin/env python3
"""Fail the build when the resolved lock contains a known vulnerability.

``pip-audit`` alone is not a policy: a single unfixable advisory would keep the
job red forever and tempt somebody to switch the check off. This wrapper makes
the policy explicit and reviewable:

* every advisory is blocking;
* an advisory may be allow-listed **only** together with the exact package, a
  written reason and a review date, in :data:`ALLOWED_ADVISORIES` below;
* an allow-listed advisory whose review date has passed becomes blocking again,
  so the exception cannot silently become permanent;
* an allow-list entry that no longer matches anything is reported, so stale
  excuses get deleted instead of accumulating;
* the report must **cover the whole lock**: if ``pip-audit`` answers with an
  empty, truncated or differently-versioned dependency list, the audit fails
  instead of printing "0 vulnerabilities". Without this invariant an empty
  report and a clean report are indistinguishable, and the check would silently
  pass the day its output format changes.

Usage::

    python tools/audit_requirements_lock.py            # CI mode
    python tools/audit_requirements_lock.py --json     # machine output

Requires ``pip-audit`` on PATH (CI installs it); it is deliberately not a
runtime dependency of the bot.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "requirements-lock.txt"
POLICY = "resolved-lock-zero-known-vulnerabilities-v1"


@dataclass(frozen=True)
class AllowedAdvisory:
    package: str
    reason: str
    review_by: dt.date


# Reviewed exceptions. Keep this list short, dated and honest.
#
# Currently empty: the last entry was pdfkit/PYSEC-2026-2860, which had no
# upstream fix. Instead of living with the exception, the package was removed —
# the code calls the wkhtmltopdf binary directly and never imported pdfkit for
# anything but an unused wrapper. Any future entry must carry a review date.
ALLOWED_ADVISORIES: dict[str, AllowedAdvisory] = {}


def locked_versions(lock_path: Path = DEFAULT_LOCK) -> dict[str, str]:
    """Canonical distribution name -> pinned version, straight from the lock."""
    locked: dict[str, str] = {}
    for raw in Path(lock_path).read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, version = line.partition("==")
        if not separator:
            raise SystemExit(f"lock entry is not an exact pin: {line}")
        name = name.split("[", 1)[0].strip()
        locked[canonicalize_name(name)] = version.strip()
    if not locked:
        raise SystemExit(f"lock file has no pinned distributions: {lock_path}")
    return locked


def _coverage_problems(payload: dict[str, object], locked: dict[str, str]) -> list[dict[str, str]]:
    """The report must describe exactly the packages and versions we ship."""
    raw_dependencies = payload.get("dependencies")
    if not isinstance(raw_dependencies, list) or not raw_dependencies:
        return [
            {
                "id": "REPORT_EMPTY",
                "package": "",
                "version": "",
                "fix": "",
                "reason": (
                    "pip-audit returned no dependencies; an empty report is "
                    "indistinguishable from a clean one, so it fails closed"
                ),
            }
        ]

    reported: dict[str, str] = {}
    for dependency in raw_dependencies:
        if not isinstance(dependency, dict):
            continue
        name = str(dependency.get("name") or "").strip()
        if not name:
            continue
        reported[canonicalize_name(name)] = str(dependency.get("version") or "")

    problems: list[dict[str, str]] = []
    for name in sorted(set(locked) - set(reported)):
        problems.append(
            {
                "id": "REPORT_MISSING_PACKAGE",
                "package": name,
                "version": locked[name],
                "fix": "",
                "reason": "locked package was not audited; the report is incomplete",
            }
        )
    for name in sorted(set(reported) & set(locked)):
        if reported[name] != locked[name]:
            problems.append(
                {
                    "id": "REPORT_VERSION_MISMATCH",
                    "package": name,
                    "version": locked[name],
                    "fix": "",
                    "reason": (
                        f"pip-audit audited {reported[name] or 'an unknown version'}, not the pinned {locked[name]}"
                    ),
                }
            )
    return problems


def _pip_audit_executable() -> str:
    executable = shutil.which("pip-audit")
    if not executable:
        raise SystemExit("pip-audit is not installed. Install it with: python -m pip install pip-audit")
    return executable


def audit(lock_path: Path = DEFAULT_LOCK, *, today: dt.date | None = None) -> dict[str, object]:
    """Return a report dict; ``blocking`` is empty when the policy passes."""
    current_day = today or dt.date.today()
    process = subprocess.run(
        [
            _pip_audit_executable(),
            "-r",
            str(lock_path),
            # The input is already the exact transitive closure. Re-resolving it
            # through pip-audit's own environment can silently omit an installed
            # pin (for example packaging itself) and makes coverage unreliable.
            "--no-deps",
            "--disable-pip",
            "--format",
            "json",
            "--progress-spinner",
            "off",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode not in {0, 1}:
        raise SystemExit(f"pip-audit failed to run: {process.stderr.strip()[:800]}")
    try:
        payload = json.loads(process.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"pip-audit did not return JSON; refusing to treat an unreadable report as a clean one: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise SystemExit("pip-audit returned a non-object JSON report")

    blocking: list[dict[str, str]] = []
    allowed: list[dict[str, str]] = []
    used: set[str] = set()
    # pip-audit can emit the same advisory twice for one package (one record per
    # alias); report and count it once.
    seen: set[tuple[str, str, str]] = set()

    for dependency in payload.get("dependencies", []):
        name = str(dependency.get("name") or "")
        version = str(dependency.get("version") or "")
        for vulnerability in dependency.get("vulns", []) or []:
            advisory = str(vulnerability.get("id") or "")
            if (advisory, name, version) in seen:
                continue
            seen.add((advisory, name, version))
            fix_versions = [str(item) for item in vulnerability.get("fix_versions") or []]
            entry = {
                "id": advisory,
                "package": name,
                "version": version,
                "fix": ", ".join(fix_versions) or "no fix published",
            }
            exception = ALLOWED_ADVISORIES.get(advisory)
            if exception is None or exception.package != name:
                blocking.append(entry)
                continue
            used.add(advisory)
            if current_day > exception.review_by:
                blocking.append(
                    {**entry, "reason": (f"allow-list review expired on {exception.review_by.isoformat()}")}
                )
                continue
            allowed.append({**entry, "reason": exception.reason})

    locked = locked_versions(lock_path)
    blocking.extend(_coverage_problems(payload, locked))

    stale = sorted(set(ALLOWED_ADVISORIES) - used)
    return {
        "policy": POLICY,
        "lock": str(lock_path.relative_to(ROOT) if lock_path.is_relative_to(ROOT) else lock_path),
        "checked_on": current_day.isoformat(),
        "locked_distributions": len(locked),
        "blocking": blocking,
        "allowed": allowed,
        "stale_allow_list_entries": stale,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--json", action="store_true", help="print the raw report")
    args = parser.parse_args(argv)

    report = audit(args.lock)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for entry in report["allowed"]:  # type: ignore[union-attr]
            print(f"ALLOWED {entry['id']} {entry['package']}=={entry['version']} (fix: {entry['fix']})")
        for entry in report["stale_allow_list_entries"]:  # type: ignore[union-attr]
            print(f"STALE allow-list entry, delete it: {entry}")
        for entry in report["blocking"]:  # type: ignore[union-attr]
            if entry["id"].startswith("REPORT_"):
                print(f"{entry['id']} {entry['package']} {entry.get('reason', '')}".rstrip())
                continue
            print(
                f"VULNERABLE {entry['id']} {entry['package']}=={entry['version']} "
                f"(fix: {entry['fix']}) {entry.get('reason', '')}".rstrip()
            )

    if report["stale_allow_list_entries"]:
        print(
            "DEPENDENCY_AUDIT_FAILED: stale allow-list entries must be removed: "
            + ", ".join(report["stale_allow_list_entries"]),  # type: ignore[arg-type]
            file=sys.stderr,
        )
        return 1
    if report["blocking"]:
        print(
            f"DEPENDENCY_AUDIT_FAILED: {len(report['blocking'])} blocking advisory(ies)",
            file=sys.stderr,
        )
        return 1
    print(
        f"dependency audit passed ({report['locked_distributions']} locked "
        f"distribution(s) audited, {len(report['allowed'])} reviewed exception(s), "
        f"{len(report['stale_allow_list_entries'])} stale)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
