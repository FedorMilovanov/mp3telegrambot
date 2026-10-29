import re
from pathlib import Path

import pytest

import scripts.required_pr_gate as required_gate
from scripts.required_pr_gate import (
    GATE_WORKFLOW,
    MissingPullRequestTrigger,
    discover_profile_workflows,
    expected_profile_workflows,
    extract_pull_request_filter,
    github_path_matches,
    workflow_applies,
)


ROOT = Path(__file__).resolve().parents[1]


def test_changed_files_requires_and_uses_a_resolved_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def check_output(command: list[str], **kwargs: object) -> str:
        commands.append(command)
        return "services/example.py\n"

    monkeypatch.setattr(required_gate.shutil, "which", lambda name: "/usr/bin/git" if name == "git" else None)
    monkeypatch.setattr(required_gate.subprocess, "check_output", check_output)

    assert required_gate.changed_files("a" * 40, "b" * 40) == ("services/example.py",)
    assert commands == [
        [
            "/usr/bin/git",
            "diff",
            "--name-only",
            "--end-of-options",
            f"{'a' * 40}...{'b' * 40}",
            "--",
        ]
    ]

    monkeypatch.setattr(required_gate.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="git executable not found"):
        required_gate.changed_files("a" * 40, "b" * 40)


def test_changed_files_rejects_non_sha_or_option_like_endpoints() -> None:
    for base_sha, head_sha in (
        ("--output=unexpected", "b" * 40),
        ("a" * 40, "-1"),
        ("a" * 39, "b" * 40),
    ):
        with pytest.raises(ValueError, match="full 40-character SHAs"):
            required_gate.changed_files(base_sha, head_sha)


def test_github_path_match_supports_repository_workflow_patterns() -> None:
    assert github_path_matches("bot.py", "*.py")
    assert not github_path_matches("services/bot.py", "*.py")
    assert github_path_matches("bot.py", "**/*.py")
    assert github_path_matches("services/bot.py", "**/*.py")
    assert github_path_matches("handlers/dub_runtime/x.py", "handlers/dub_*/**/*.py")
    assert github_path_matches("Start Bot.bat", "Start Bot.bat")
    assert not github_path_matches("README.md", "**/*.py")


def test_extract_pull_request_filter_handles_paths_and_paths_ignore() -> None:
    paths_mode, paths = extract_pull_request_filter(
        """on:\n  pull_request:\n    branches: [\"main\"]\n    paths:\n      - \"**/*.py\"\n      - '.env.example'\n"""
    )
    assert paths_mode == "paths"
    assert paths == ("**/*.py", ".env.example")

    ignore_mode, ignored = extract_pull_request_filter(
        """on:\n  pull_request:\n    paths-ignore:\n      - \"**/*.md\"\n      - .gitignore\n"""
    )
    assert ignore_mode == "paths-ignore"
    assert ignored == ("**/*.md", ".gitignore")


def test_workflow_applies_matches_github_trigger_semantics() -> None:
    assert workflow_applies("paths", ("**/*.py",), ("services/x.py",))
    assert not workflow_applies("paths", ("**/*.py",), ("README.md",))
    assert workflow_applies("paths-ignore", ("**/*.md", ".gitignore"), ("README.md", "bot.py"))
    assert not workflow_applies("paths-ignore", ("**/*.md", ".gitignore"), ("README.md", ".gitignore"))
    with pytest.raises(ValueError, match="negative patterns"):
        workflow_applies("paths-ignore", ("!README.md",), ("README.md",))


def test_every_pull_request_workflow_is_gated() -> None:
    """A red PR check must be able to block a merge.

    The gate used to hard-code four workflows, so six PR workflows were checked
    by nobody. Discovery is derived from the directory, so this asserts the two
    independent views agree.
    """
    declared = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / ".github" / "workflows").glob("*.yml")
        if path.name != Path(GATE_WORKFLOW).name and "pull_request:" in path.read_text(encoding="utf-8")
    }
    discovered = set(discover_profile_workflows(ROOT))

    assert discovered == declared
    assert GATE_WORKFLOW not in discovered
    assert ".github/workflows/voxcpm2-windows.yml" in discovered
    assert ".github/workflows/windows-full-suite-regressions.yml" in discovered


def test_discovery_picks_up_a_new_workflow_without_registration(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("on:\n  pull_request:\n", encoding="utf-8")
    (workflows / "existing.yml").write_text(
        'on:\n  pull_request:\n    paths:\n      - "services/**"\n',
        encoding="utf-8",
    )
    (workflows / "brand-new.yml").write_text(
        'on:\n  pull_request:\n    paths:\n      - "core/**"\n',
        encoding="utf-8",
    )
    (workflows / "push-only.yml").write_text("on:\n  push:\n    branches: [main]\n", encoding="utf-8")
    (workflows / "notes.txt").write_text("not a workflow", encoding="utf-8")

    assert discover_profile_workflows(tmp_path) == (
        ".github/workflows/brand-new.yml",
        ".github/workflows/existing.yml",
    )


def test_workflow_without_pull_request_trigger_is_not_gated(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "release.yml").write_text("on:\n  workflow_dispatch:\n", encoding="utf-8")

    with pytest.raises(MissingPullRequestTrigger):
        extract_pull_request_filter((workflows / "release.yml").read_text(encoding="utf-8"))
    with pytest.raises(RuntimeError, match="no pull_request workflows"):
        discover_profile_workflows(tmp_path)


def test_required_profile_workflow_filters_are_parseable_and_supported() -> None:
    for workflow in discover_profile_workflows(ROOT):
        path = ROOT / workflow
        assert path.is_file(), workflow
        mode, patterns = extract_pull_request_filter(path.read_text(encoding="utf-8"))
        assert mode in {"all", "paths", "paths-ignore"}
        if mode != "all":
            assert patterns
        for pattern in patterns:
            github_path_matches("sentinel/path.py", pattern.lstrip("!"))


def test_expected_profile_workflows_use_live_workflow_filters() -> None:
    # Документация не запускает профильные workflow, но secret-scan смотрит
    # каждый PR без фильтров: токен чаще всего утекает именно в markdown-примере
    # или в .env-шаблоне, а не в коде.
    secret_scan = {".github/workflows/secret-scan.yml"}
    assert set(expected_profile_workflows(ROOT, ("README.md",))) == secret_scan

    youtube = set(expected_profile_workflows(ROOT, ("services/youtube_po_token_runtime.py",)))
    assert {
        ".github/workflows/cut-policy-ci.yml",
        ".github/workflows/gemini-qa-policy.yml",
        ".github/workflows/windows-bootstrap-ci.yml",
    } <= youtube

    dub = set(expected_profile_workflows(ROOT, ("services/dub_example.py",)))
    assert {
        ".github/workflows/gemini-qa-policy.yml",
        ".github/workflows/dub-studio-checks.yml",
    } <= dub

    # Anything selected must be a real, PR-triggered workflow.
    assert youtube | dub <= set(discover_profile_workflows(ROOT))

    # requirements-lock.txt is also watched by the TTS model-catalog workflow.
    lock = set(expected_profile_workflows(ROOT, ("requirements-lock.txt",)))
    assert {
        ".github/workflows/cut-policy-ci.yml",
        ".github/workflows/windows-bootstrap-ci.yml",
        ".github/workflows/dub-studio-checks.yml",
        ".github/workflows/voxcpm2-windows.yml",
    } <= lock

    # .env.example — не документация: его смотрит и CodeQL (любой не-md файл
    # запускает анализ), и secret-scan, и политика Gemini-ключей.
    assert (
        set(expected_profile_workflows(ROOT, (".env.example",)))
        == {
            ".github/workflows/gemini-qa-policy.yml",
            ".github/workflows/codeql.yml",
        }
        | secret_scan
    )


def test_wait_timeout_fits_inside_the_gate_job_budget() -> None:
    """The script must not give up before the job that runs it.

    The wait also covers queueing for the slowest profile workflow, so a
    script-side timeout smaller than the job budget produces false reds that
    look like gate failures. The number used to be 1500s (25 min) against a
    45-minute job; the explicit `--timeout-seconds 2400` in `ci.yml` and this
    default have to agree with the workflow file.
    """
    from scripts.required_pr_gate import DEFAULT_TIMEOUT_SECONDS

    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    gate_block = ci.split("required-pr-gate:", 1)[1]
    budget_match = re.search(r"(?m)^\s*timeout-minutes:\s*(\d+)", gate_block)
    assert budget_match, "required-pr-gate must declare timeout-minutes"
    budget_seconds = int(budget_match.group(1)) * 60

    assert DEFAULT_TIMEOUT_SECONDS <= budget_seconds, "the gate script would expire before its own job"
    assert budget_seconds - DEFAULT_TIMEOUT_SECONDS >= 300, (
        "leave at least 5 minutes for checkout, job setup and reporting"
    )
    assert f"--timeout-seconds {DEFAULT_TIMEOUT_SECONDS}" in ci, (
        "ci.yml must pass the same budget explicitly instead of relying on a default"
    )
