from pathlib import Path

import pytest

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


def test_github_path_match_supports_repository_workflow_patterns():
    assert github_path_matches("bot.py", "*.py")
    assert not github_path_matches("services/bot.py", "*.py")
    assert github_path_matches("bot.py", "**/*.py")
    assert github_path_matches("services/bot.py", "**/*.py")
    assert github_path_matches("handlers/dub_runtime/x.py", "handlers/dub_*/**/*.py")
    assert github_path_matches("Start Bot.bat", "Start Bot.bat")
    assert not github_path_matches("README.md", "**/*.py")


def test_extract_pull_request_filter_handles_paths_and_paths_ignore():
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


def test_workflow_applies_matches_github_trigger_semantics():
    assert workflow_applies("paths", ("**/*.py",), ("services/x.py",))
    assert not workflow_applies("paths", ("**/*.py",), ("README.md",))
    assert workflow_applies(
        "paths-ignore", ("**/*.md", ".gitignore"), ("README.md", "bot.py")
    )
    assert not workflow_applies(
        "paths-ignore", ("**/*.md", ".gitignore"), ("README.md", ".gitignore")
    )
    with pytest.raises(ValueError, match="negative patterns"):
        workflow_applies("paths-ignore", ("!README.md",), ("README.md",))


def test_every_pull_request_workflow_is_gated():
    """A red PR check must be able to block a merge.

    The gate used to hard-code four workflows, so six PR workflows were checked
    by nobody. Discovery is derived from the directory, so this asserts the two
    independent views agree.
    """
    declared = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / ".github" / "workflows").glob("*.yml")
        if path.name != Path(GATE_WORKFLOW).name
        and "pull_request:" in path.read_text(encoding="utf-8")
    }
    discovered = set(discover_profile_workflows(ROOT))

    assert discovered == declared
    assert GATE_WORKFLOW not in discovered
    assert ".github/workflows/voxcpm2-windows.yml" in discovered
    assert ".github/workflows/windows-full-suite-regressions.yml" in discovered


def test_discovery_picks_up_a_new_workflow_without_registration(tmp_path):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text(
        "on:\n  pull_request:\n", encoding="utf-8"
    )
    (workflows / "existing.yml").write_text(
        "on:\n  pull_request:\n    paths:\n      - \"services/**\"\n",
        encoding="utf-8",
    )
    (workflows / "brand-new.yml").write_text(
        "on:\n  pull_request:\n    paths:\n      - \"core/**\"\n",
        encoding="utf-8",
    )
    (workflows / "push-only.yml").write_text(
        "on:\n  push:\n    branches: [main]\n", encoding="utf-8"
    )
    (workflows / "notes.txt").write_text("not a workflow", encoding="utf-8")

    assert discover_profile_workflows(tmp_path) == (
        ".github/workflows/brand-new.yml",
        ".github/workflows/existing.yml",
    )


def test_workflow_without_pull_request_trigger_is_not_gated(tmp_path):
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "release.yml").write_text(
        "on:\n  workflow_dispatch:\n", encoding="utf-8"
    )

    with pytest.raises(MissingPullRequestTrigger):
        extract_pull_request_filter(
            (workflows / "release.yml").read_text(encoding="utf-8")
        )
    with pytest.raises(RuntimeError, match="no pull_request workflows"):
        discover_profile_workflows(tmp_path)


def test_required_profile_workflow_filters_are_parseable_and_supported():
    for workflow in discover_profile_workflows(ROOT):
        path = ROOT / workflow
        assert path.is_file(), workflow
        mode, patterns = extract_pull_request_filter(path.read_text(encoding="utf-8"))
        assert mode in {"all", "paths", "paths-ignore"}
        if mode != "all":
            assert patterns
        for pattern in patterns:
            github_path_matches("sentinel/path.py", pattern.lstrip("!"))


def test_expected_profile_workflows_use_live_workflow_filters():
    assert set(expected_profile_workflows(ROOT, ("README.md",))) == set()

    youtube = set(
        expected_profile_workflows(ROOT, ("services/youtube_po_token_runtime.py",))
    )
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

    assert set(expected_profile_workflows(ROOT, (".env.example",))) == {
        ".github/workflows/gemini-qa-policy.yml"
    }
