from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"


def test_every_github_actions_workflow_is_valid_and_structured_yaml() -> None:
    workflow_paths = sorted(
        [*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")],
        key=lambda path: path.as_posix(),
    )
    assert workflow_paths, "No GitHub Actions workflow files were found."

    failures: list[str] = []
    for path in workflow_paths:
        relative = path.relative_to(ROOT)
        try:
            source = path.read_text(encoding="utf-8")
            document = yaml.safe_load(source)
            literal_keys = yaml.load(source, Loader=yaml.BaseLoader)
        except (UnicodeError, OSError, yaml.YAMLError) as exc:
            failures.append(f"{relative}: {type(exc).__name__}: {exc}")
            continue

        if not isinstance(document, dict) or not isinstance(literal_keys, dict):
            failures.append(f"{relative}: top-level YAML document must be a mapping")
            continue
        if "on" not in literal_keys:
            failures.append(f"{relative}: missing top-level 'on' trigger mapping")
        jobs = literal_keys.get("jobs")
        if not isinstance(jobs, dict) or not jobs:
            failures.append(f"{relative}: missing non-empty top-level 'jobs' mapping")

    assert not failures, "Invalid GitHub Actions workflow YAML:\n" + "\n".join(failures)
