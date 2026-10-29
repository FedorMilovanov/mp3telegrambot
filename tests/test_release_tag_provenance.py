from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "tools" / "verify_release_tag.py"
TAG = "v1.2.3"


def _git(cwd: Path, *args: str) -> str:
    executable = shutil.which("git")
    assert executable is not None, "git is required for release provenance tests"
    completed = subprocess.run(
        [executable, *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _commit_file(cwd: Path, name: str, content: str, message: str) -> str:
    (cwd / name).write_text(content, encoding="utf-8")
    _git(cwd, "add", name)
    _git(cwd, "commit", "-m", message)
    return _git(cwd, "rev-parse", "HEAD")


def _make_tagged_remote(tmp_path: Path, *, annotated: bool) -> tuple[Path, str, str]:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "--initial-branch=main")

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "--initial-branch=main")
    _git(seed, "config", "user.name", "Release Test")
    _git(seed, "config", "user.email", "release-test@example.invalid")
    tagged_commit = _commit_file(seed, "source.txt", "tagged\n", "tagged source")
    if annotated:
        _git(seed, "tag", "--annotate", TAG, "--message", "release")
    else:
        _git(seed, "tag", TAG)
    default_commit = _commit_file(seed, "source.txt", "default\n", "default branch advanced")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "origin", "main", TAG)

    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", str(remote), str(checkout))
    assert _git(checkout, "rev-parse", "HEAD") == default_commit
    return checkout, tagged_commit, default_commit


def _verify(cwd: Path, output: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GITHUB_OUTPUT"] = str(output)
    return subprocess.run(
        [sys.executable, str(VERIFIER), "--tag", TAG],
        cwd=cwd,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


@pytest.mark.parametrize("annotated", [True, False], ids=["annotated", "lightweight"])
def test_requested_tag_checkout_is_bound_to_remote_commit(tmp_path: Path, annotated: bool) -> None:
    checkout, tagged_commit, _ = _make_tagged_remote(tmp_path, annotated=annotated)
    _git(checkout, "checkout", "--detach", TAG)
    output = tmp_path / "github-output.txt"

    completed = _verify(checkout, output)

    assert completed.returncode == 0, completed.stderr
    assert output.read_text(encoding="utf-8").splitlines() == [
        f"commit_sha={tagged_commit}",
        f"tag={TAG}",
    ]


def test_default_branch_checkout_cannot_be_published_as_requested_tag(tmp_path: Path) -> None:
    checkout, tagged_commit, default_commit = _make_tagged_remote(tmp_path, annotated=True)
    assert tagged_commit != default_commit
    output = tmp_path / "github-output.txt"

    completed = _verify(checkout, output)

    assert completed.returncode == 1
    assert "release source mismatch" in completed.stderr
    assert f"HEAD={default_commit}" in completed.stderr
    assert not output.exists()


def test_remote_tag_move_is_rejected_even_when_local_tag_matches_head(tmp_path: Path) -> None:
    checkout, tagged_commit, default_commit = _make_tagged_remote(tmp_path, annotated=True)
    _git(checkout, "checkout", "--detach", TAG)
    seed = tmp_path / "seed"
    _git(seed, "push", "--force", "origin", f"{default_commit}:refs/tags/{TAG}")

    completed = _verify(checkout, tmp_path / "github-output.txt")

    assert completed.returncode == 1
    assert "release source mismatch" in completed.stderr
    assert f"HEAD={tagged_commit}" in completed.stderr
    assert f"remote peeled tag={default_commit}" in completed.stderr


def test_tag_is_validated_before_any_git_lookup(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("GITHUB_OUTPUT", None)
    completed = subprocess.run(
        [sys.executable, str(VERIFIER), "--tag", "../../main"],
        cwd=tmp_path,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 1
    assert "invalid release tag" in completed.stderr
