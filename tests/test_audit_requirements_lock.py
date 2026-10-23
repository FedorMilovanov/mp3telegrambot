"""Policy tests for ``tools/audit_requirements_lock.py``.

The tool must fail on any known vulnerability, allow only reviewed exceptions
with an unexpired review date, and reject stale allow-list entries. These tests
drive it through a fake ``pip-audit`` so they stay offline.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import audit_requirements_lock as audit_tool

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements-lock.txt"


def _locked() -> dict[str, str]:
    return audit_tool.locked_versions(LOCK)


def _report(
    vulns: list[dict[str, object]], package: str = "pdfkit"
) -> str:
    """A report that covers the whole lock, with ``vulns`` on one package.

    Coverage is part of the policy now, so a fake report must look like a real
    one: every locked distribution, at the pinned version.
    """
    locked = _locked()
    assert package in locked, f"{package} is not in the lock"
    return json.dumps(
        {
            "dependencies": [
                {"name": name, "version": version,
                 "vulns": vulns if name == package else []}
                for name, version in sorted(locked.items())
            ]
        }
    )


@pytest.fixture()
def fake_pip_audit(monkeypatch: pytest.MonkeyPatch):
    state: dict[str, object] = {"stdout": _report([]), "returncode": 1}

    def fake_run(_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=state["returncode"], stdout=state["stdout"], stderr=""
        )

    monkeypatch.setattr(audit_tool.shutil, "which", lambda _name: "/usr/bin/pip-audit")
    monkeypatch.setattr(audit_tool.subprocess, "run", fake_run)
    return state


def test_clean_lock_has_no_blockers_but_flags_an_unused_exception(
    fake_pip_audit,
) -> None:
    """A real clean report also means every exception is obsolete."""
    report = audit_tool.audit(LOCK)
    assert report["blocking"] == []
    assert report["stale_allow_list_entries"] == sorted(
        audit_tool.ALLOWED_ADVISORIES
    )


def test_unknown_vulnerability_is_blocking(fake_pip_audit) -> None:
    fake_pip_audit["stdout"] = _report(
        [{"id": "CVE-2099-0001", "fix_versions": ["1.0.1"]}], package="requests"
    )
    report = audit_tool.audit(LOCK)
    assert [entry["id"] for entry in report["blocking"]] == ["CVE-2099-0001"]
    assert audit_tool.main([]) == 1


def test_the_same_advisory_reported_twice_is_counted_once(fake_pip_audit) -> None:
    """pip-audit emits one record per alias; a duplicate must not inflate counts."""
    fake_pip_audit["stdout"] = _report(
        [
            {"id": "PYSEC-2026-2860", "fix_versions": []},
            {"id": "PYSEC-2026-2860", "fix_versions": []},
        ],
        package="pdfkit",
    )
    report = audit_tool.audit(LOCK)
    assert len(report["allowed"]) == 1
    assert report["blocking"] == []


def test_reviewed_exception_is_allowed_until_its_review_date(fake_pip_audit) -> None:
    fake_pip_audit["stdout"] = _report(
        [{"id": "PYSEC-2026-2860", "fix_versions": []}], package="pdfkit"
    )

    fresh = audit_tool.audit(LOCK, today=dt.date(2026, 10, 23))
    assert fresh["blocking"] == []
    assert len(fresh["allowed"]) == 1

    expired = audit_tool.audit(LOCK, today=dt.date(2030, 1, 1))
    assert expired["blocking"]
    assert "review expired" in expired["blocking"][0]["reason"]


def test_exception_does_not_cover_a_different_package(fake_pip_audit) -> None:
    fake_pip_audit["stdout"] = _report(
        [{"id": "PYSEC-2026-2860", "fix_versions": []}], package="requests"
    )
    report = audit_tool.audit(LOCK)
    assert report["blocking"]


def test_exception_for_an_absent_advisory_is_stale(fake_pip_audit) -> None:
    report = audit_tool.audit(LOCK)
    assert report["stale_allow_list_entries"] == ["PYSEC-2026-2860"]
    assert audit_tool.main([]) == 1


def test_empty_report_fails_closed(fake_pip_audit) -> None:
    """An empty report and a clean report must not look the same."""
    for payload in ("{}", '{"dependencies": []}', ""):
        fake_pip_audit["stdout"] = payload
        report = audit_tool.audit(LOCK)
        assert [entry["id"] for entry in report["blocking"]] == ["REPORT_EMPTY"], payload


def test_report_missing_a_locked_package_fails_closed(fake_pip_audit) -> None:
    payload = json.loads(_report([]))
    payload["dependencies"] = [
        item for item in payload["dependencies"] if item["name"] != "requests"
    ]
    fake_pip_audit["stdout"] = json.dumps(payload)

    report = audit_tool.audit(LOCK)
    assert [entry["id"] for entry in report["blocking"]] == ["REPORT_MISSING_PACKAGE"]
    assert report["blocking"][0]["package"] == "requests"


def test_report_auditing_a_different_version_fails_closed(fake_pip_audit) -> None:
    payload = json.loads(_report([]))
    for item in payload["dependencies"]:
        if item["name"] == "pillow":
            item["version"] = "11.3.0"
    fake_pip_audit["stdout"] = json.dumps(payload)

    report = audit_tool.audit(LOCK)
    assert [entry["id"] for entry in report["blocking"]] == ["REPORT_VERSION_MISMATCH"]
    assert "11.3.0" in report["blocking"][0]["reason"]


def test_unreadable_report_is_not_treated_as_clean(fake_pip_audit) -> None:
    fake_pip_audit["stdout"] = "WARNING: something went wrong"
    with pytest.raises(SystemExit, match="did not return JSON"):
        audit_tool.audit(LOCK)


def test_renamed_report_key_fails_closed(fake_pip_audit) -> None:
    fake_pip_audit["stdout"] = json.dumps({"deps": [{"name": "requests", "version": "1"}]})
    report = audit_tool.audit(LOCK)
    assert [entry["id"] for entry in report["blocking"]] == ["REPORT_EMPTY"]


def test_coverage_survives_a_name_normalisation_difference(fake_pip_audit) -> None:
    payload = json.loads(_report([]))
    for item in payload["dependencies"]:
        if item["name"] == "python-dotenv":
            item["name"] = "python_dotenv"
    fake_pip_audit["stdout"] = json.dumps(payload)
    assert audit_tool.audit(LOCK)["blocking"] == []


def test_locked_versions_reads_every_pin() -> None:
    locked = audit_tool.locked_versions(LOCK)
    assert len(locked) == 70
    assert locked["pillow"].startswith("13.")
    assert all(locked.values()), "every entry must carry a version"


def test_allow_list_entries_carry_a_reason_and_a_review_date() -> None:
    for advisory, entry in audit_tool.ALLOWED_ADVISORIES.items():
        assert entry.package, advisory
        assert len(entry.reason) > 40, advisory
        assert entry.review_by > dt.date(2026, 10, 23), advisory


def test_missing_pip_audit_fails_with_an_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit_tool.shutil, "which", lambda _name: None)
    with pytest.raises(SystemExit, match="pip-audit is not installed"):
        audit_tool.audit(LOCK)


def test_the_audit_is_reusable_and_reaches_both_pr_and_schedule() -> None:
    """One definition, two callers: pull requests and the daily schedule.

    Duplicating the steps between `ci.yml` and a scheduled workflow would let
    the two drift, and a scheduled audit that differs from the blocking one is
    worse than none: it reports on a pipeline nobody gates on.
    """
    audit_workflow = (
        ROOT / ".github" / "workflows" / "dependency-audit.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_call:" in audit_workflow
    assert "schedule:" in audit_workflow, "a new advisory must not wait for a push"
    assert "cron:" in audit_workflow
    assert "python tools/check_requirements_lock.py" in audit_workflow
    assert "python tools/audit_requirements_lock.py" in audit_workflow


def test_ci_runs_the_audit_inside_the_required_gate() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "uses: ./.github/workflows/dependency-audit.yml" in ci
    assert "dependency-audit:" in ci
    assert "- dependency-audit" in ci.split("required-pr-gate:")[1].split("runs-on")[0]
    assert 'test "$AUDIT_RESULT" = "success"' in ci


def test_ci_matrix_covers_every_supported_python() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert 'python-version: ["3.11", "3.12", "3.13"]' in ci


def test_dependabot_covers_every_ecosystem() -> None:
    config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert 'package-ecosystem: "pip"' in config
    assert 'package-ecosystem: "npm"' in config
    assert 'package-ecosystem: "github-actions"' in config
    # A global allow-list is what kept most pins from ever being proposed.
    assert "allow:" not in config
    assert "open-pull-requests-limit: 1\n" not in config
    assert (ROOT / "vot_helper" / "package-lock.json").is_file()


def test_tool_is_importable_by_the_runtime_interpreter() -> None:
    """No third-party import at module level: CI installs pip-audit separately."""
    source = (ROOT / "tools" / "audit_requirements_lock.py").read_text(encoding="utf-8")
    assert "import pip_audit" not in source
    assert sys.version_info >= (3, 11)
