"""No-provider regression for local Telegram Bot API repair authorization.

Only the early fail-closed script path is executed, using an absent .env.
Never launch an actual repair, cloud logOut, process stop, or local bot server.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "repair-local-bot-api.ps1"


def _shell() -> str:
    binary = shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
    if not binary:
        pytest.skip("PowerShell not available; static guard inspection still runs")
    return binary


def _safe_call(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Nonexistent env proves safe-mode and the missing-confirmation guard do
    # not read any credentials. The high-risk execution branch is never entered.
    command = [_shell(), "-NoProfile", "-NonInteractive"]
    if os.name == "nt":
        # Windows PowerShell 5.1 often defaults to Restricted. This process-
        # scoped setting does not change the user's execution-policy profile.
        command.extend(["-ExecutionPolicy", "Bypass"])
    command.extend(
        ["-File", str(SCRIPT), "-EnvFile", str(tmp_path / "no-local-bot-api-credentials.env"), *args]
    )
    return subprocess.run(
        command,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_guard_is_before_config_and_network_side_effects() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    gate = source.index("if (-not $Execute) {")
    cloud_guard = source.index("if (-not $SkipCloudLogout -and -not $ConfirmCloudLogout) {")
    env_read = source.index("$dotenv = Read-DotEnv $EnvFile")
    assert gate < cloud_guard < env_read
    assert "[switch]$Execute" in source
    assert "[switch]$ConfirmCloudLogout" in source
    assert "throw \"Cloud Bot API logOut requires explicit -ConfirmCloudLogout" in source


@pytest.mark.parametrize("args", [(), ("-ApplyConfig",), ("-SkipCloudLogout",)])
def test_default_and_config_only_are_noop_before_reading_env(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    result = _safe_call(tmp_path, *args)
    assert result.returncode == 0, result.stderr
    assert "SAFE MODE" in result.stdout
    assert not list(tmp_path.iterdir())


def test_execute_without_cloud_logout_confirmation_blocks_before_env(tmp_path: Path) -> None:
    result = _safe_call(tmp_path, "-Execute", "-ApplyConfig")
    assert result.returncode != 0
    assert "requires explicit -ConfirmCloudLogout" in (result.stderr + result.stdout)
    assert not list(tmp_path.iterdir())


def test_operator_docs_never_show_config_apply_without_explicit_authorization() -> None:
    for relative in ("docs/local-bot-api-repair.md", "docs/ops/deploy_windows.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "repair-local-bot-api.ps1 -ApplyConfig -Execute -ConfirmCloudLogout" in text
