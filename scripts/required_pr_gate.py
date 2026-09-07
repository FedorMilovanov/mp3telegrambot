from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

PROFILE_WORKFLOWS = (
    ".github/workflows/cut-policy-ci.yml",
    ".github/workflows/gemini-qa-policy.yml",
    ".github/workflows/windows-bootstrap-ci.yml",
    ".github/workflows/dub-studio-checks.yml",
)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _yaml_list_scalar(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    if not value:
        raise ValueError("empty workflow path pattern")
    return value


def extract_pull_request_filter(workflow_text: str) -> tuple[str, tuple[str, ...]]:
    """Return (mode, patterns) for a workflow pull_request trigger.

    mode is one of: all, paths, paths-ignore. The parser is intentionally narrow
    and fails closed when the expected block structure cannot be understood.
    """

    lines = workflow_text.splitlines()
    on_index = next(
        (index for index, line in enumerate(lines) if line.strip() == "on:" and _indent(line) == 0),
        None,
    )
    if on_index is None:
        raise ValueError("workflow is missing a top-level on: block")

    pull_index: int | None = None
    for index in range(on_index + 1, len(lines)):
        line = lines[index]
        stripped = line.strip()
        if stripped and _indent(line) == 0:
            break
        if _indent(line) == 2 and stripped.startswith("pull_request:"):
            pull_index = index
            break
    if pull_index is None:
        raise ValueError("workflow is missing pull_request trigger")

    mode = "all"
    patterns: list[str] = []
    index = pull_index + 1
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        indent = _indent(line)
        if stripped and indent <= 2:
            break
        if indent == 4 and stripped in {"paths:", "paths-ignore:"}:
            mode = stripped[:-1]
            index += 1
            while index < len(lines):
                item_line = lines[index]
                item = item_line.strip()
                item_indent = _indent(item_line)
                if item and item_indent <= 4:
                    break
                if item.startswith("- "):
                    patterns.append(_yaml_list_scalar(item[2:]))
                index += 1
            break
        index += 1

    if mode != "all" and not patterns:
        raise ValueError(f"{mode} trigger has no path patterns")
    return mode, tuple(patterns)


def _glob_regex(pattern: str) -> re.Pattern[str]:
    if "{" in pattern or "}" in pattern or "[" in pattern or "]" in pattern:
        raise ValueError(f"unsupported GitHub path glob syntax: {pattern}")

    parts: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
            continue
        if pattern.startswith("**", index):
            parts.append(".*")
            index += 2
            continue

        char = pattern[index]
        if char == "*":
            parts.append("[^/]*")
        elif char == "?":
            parts.append("[^/]")
        else:
            parts.append(re.escape(char))
        index += 1

    return re.compile("^" + "".join(parts) + "$")


def github_path_matches(path: str, pattern: str) -> bool:
    return bool(_glob_regex(pattern).match(path.replace("\\", "/")))


def _selected_by_paths(path: str, patterns: tuple[str, ...]) -> bool:
    selected = False
    for pattern in patterns:
        negated = pattern.startswith("!")
        candidate = pattern[1:] if negated else pattern
        if github_path_matches(path, candidate):
            selected = not negated
    return selected


def workflow_applies(mode: str, patterns: tuple[str, ...], changed: tuple[str, ...]) -> bool:
    if mode == "all":
        return True
    if mode == "paths":
        return any(_selected_by_paths(path, patterns) for path in changed)
    if mode == "paths-ignore":
        if any(pattern.startswith("!") for pattern in patterns):
            raise ValueError("negative patterns are unsupported in paths-ignore")
        return any(
            not any(github_path_matches(path, pattern) for pattern in patterns)
            for path in changed
        )
    raise ValueError(f"unsupported trigger mode: {mode}")


def expected_profile_workflows(root: Path, changed: tuple[str, ...]) -> tuple[str, ...]:
    expected: list[str] = []
    for workflow in PROFILE_WORKFLOWS:
        workflow_path = root / workflow
        if not workflow_path.is_file():
            raise FileNotFoundError(f"required profile workflow is missing: {workflow}")
        mode, patterns = extract_pull_request_filter(workflow_path.read_text(encoding="utf-8"))
        if workflow_applies(mode, patterns, changed):
            expected.append(workflow)
    return tuple(expected)


def changed_files(base_sha: str, head_sha: str) -> tuple[str, ...]:
    output = subprocess.check_output(
        ["git", "diff", "--name-only", f"{base_sha}...{head_sha}"],
        text=True,
    )
    files = tuple(line.strip() for line in output.splitlines() if line.strip())
    if not files:
        raise RuntimeError("pull request diff unexpectedly contains no changed files")
    return files


def _github_json(url: str, token: str) -> dict[str, object]:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mp3telegrambot-required-pr-gate",
        },
    )
    with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed GitHub API URL
        return json.loads(response.read().decode("utf-8"))


def _latest_profile_runs(
    repository: str,
    head_sha: str,
    expected: tuple[str, ...],
    token: str,
) -> dict[str, dict[str, object]]:
    query = urlencode(
        {
            "head_sha": head_sha,
            "event": "pull_request",
            "per_page": 100,
        }
    )
    payload = _github_json(
        f"https://api.github.com/repos/{repository}/actions/runs?{query}", token
    )
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise RuntimeError("GitHub Actions API returned no workflow_runs list")

    latest: dict[str, dict[str, object]] = {}
    expected_set = set(expected)
    for raw_run in runs:
        if not isinstance(raw_run, dict):
            continue
        path = raw_run.get("path")
        if path not in expected_set:
            continue
        current = latest.get(path)
        if current is None or int(raw_run.get("id", 0)) > int(current.get("id", 0)):
            latest[path] = raw_run
    return latest


def wait_for_profile_workflows(
    repository: str,
    head_sha: str,
    expected: tuple[str, ...],
    token: str,
    *,
    timeout_seconds: int = 1500,
    poll_seconds: int = 10,
) -> None:
    if not expected:
        print("Required PR Gate: no profile-specific workflows apply to this diff.")
        return

    deadline = time.monotonic() + timeout_seconds
    while True:
        latest = _latest_profile_runs(repository, head_sha, expected, token)
        missing = [path for path in expected if path not in latest]
        pending: list[str] = []
        failures: list[str] = []

        for path in expected:
            run = latest.get(path)
            if run is None:
                continue
            status = str(run.get("status"))
            conclusion = run.get("conclusion")
            if status != "completed":
                pending.append(f"{path} ({status})")
            elif conclusion != "success":
                failures.append(f"{path} ({conclusion})")

        if failures:
            raise RuntimeError(
                "applicable profile workflow failed: " + ", ".join(failures)
            )
        if not missing and not pending:
            print(
                "Required PR Gate: applicable profile workflows are green: "
                + ", ".join(expected)
            )
            return
        if time.monotonic() >= deadline:
            details = [*(f"missing {path}" for path in missing), *pending]
            raise TimeoutError(
                "timed out waiting for applicable profile workflows: "
                + ", ".join(details)
            )

        waiting = [*(f"missing {path}" for path in missing), *pending]
        print("Required PR Gate: waiting for " + ", ".join(waiting), flush=True)
        time.sleep(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wait for every profile workflow applicable to this PR diff."
    )
    parser.add_argument("--timeout-seconds", type=int, default=1500)
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args()

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    repository = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    if not event_path or not repository or not token:
        raise RuntimeError(
            "GITHUB_EVENT_PATH, GITHUB_REPOSITORY and GITHUB_TOKEN are required"
        )

    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, dict):
        raise RuntimeError("Required PR Gate must run from a pull_request event")

    base = pull_request.get("base")
    head = pull_request.get("head")
    if not isinstance(base, dict) or not isinstance(head, dict):
        raise RuntimeError("pull_request event is missing base/head metadata")
    base_sha = str(base.get("sha", ""))
    head_sha = str(head.get("sha", ""))
    if not base_sha or not head_sha:
        raise RuntimeError("pull_request event is missing base/head SHA")

    root = Path.cwd()
    changed = changed_files(base_sha, head_sha)
    expected = expected_profile_workflows(root, changed)
    print("Required PR Gate changed files:")
    for path in changed:
        print(f"  - {path}")
    print("Required PR Gate expected profile workflows:")
    for path in expected:
        print(f"  - {path}")

    wait_for_profile_workflows(
        repository,
        head_sha,
        expected,
        token,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
