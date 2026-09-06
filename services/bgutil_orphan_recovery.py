#!/usr/bin/env python3
"""Safely recover an orphaned repo-local bgutil HTTP provider on Windows.

The production bot owns a single bgutil HTTP provider on localhost:4416. A hard
termination of the Python parent can leave the Node child alive. Startup may
self-heal only when the listener can be proven to be this repository's exact
``server/build/main.js`` command. Unknown listeners remain fail-closed.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PORT = 4416
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SERVER_MAIN = (
    PROJECT_ROOT
    / ".runtime"
    / "bgutil-ytdlp-pot-provider"
    / "server"
    / "build"
    / "main.js"
)
_PROBE_TIMEOUT_SEC = 10
_STOP_TIMEOUT_SEC = 5.0


class BgutilOrphanRecoveryError(RuntimeError):
    """Raised when the occupied bgutil port cannot be reconciled safely."""


@dataclass(frozen=True)
class WindowsPortOwner:
    pid: int
    name: str
    executable_path: str
    command_line: str


def _powershell_executable() -> str:
    shell = shutil.which("pwsh") or shutil.which("powershell") or shutil.which("powershell.exe")
    if not shell:
        raise BgutilOrphanRecoveryError(
            "PowerShell не найден; безопасно определить владельца bgutil-порта невозможно"
        )
    return shell


def _probe_windows_port_owners(port: int) -> tuple[WindowsPortOwner, ...]:
    shell = _powershell_executable()
    script = (
        f"$ids=@(Get-NetTCPConnection -LocalPort {int(port)} -State Listen "
        "-ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique); "
        "$rows=foreach($ownerId in $ids){"
        "$p=Get-CimInstance Win32_Process -Filter \"ProcessId=$ownerId\" -ErrorAction SilentlyContinue; "
        "if($null -ne $p){[pscustomobject]@{pid=[int]$p.ProcessId;name=[string]$p.Name;"
        "executable_path=[string]$p.ExecutablePath;command_line=[string]$p.CommandLine}}}; "
        "@($rows)|ConvertTo-Json -Compress"
    )
    try:
        process = subprocess.run(
            [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BgutilOrphanRecoveryError(
            "Не удалось определить процесс, занявший bgutil HTTP port"
        ) from exc

    if process.returncode:
        detail = (process.stderr or process.stdout or "").strip()[-700:]
        suffix = f": {detail}" if detail else ""
        raise BgutilOrphanRecoveryError(
            "PowerShell не смог определить владельца bgutil HTTP port" + suffix
        )

    raw = (process.stdout or "").strip()
    if not raw:
        return ()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BgutilOrphanRecoveryError(
            "PowerShell вернул повреждённые данные о владельце bgutil HTTP port"
        ) from exc

    rows = payload if isinstance(payload, list) else [payload]
    owners: dict[int, WindowsPortOwner] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get("pid") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        owners[pid] = WindowsPortOwner(
            pid=pid,
            name=str(row.get("name") or ""),
            executable_path=str(row.get("executable_path") or ""),
            command_line=str(row.get("command_line") or ""),
        )
    return tuple(owners[pid] for pid in sorted(owners))


def _normalized_command_text(value: str) -> str:
    return str(value or "").replace('"', "").replace("'", "").replace("/", "\\").casefold()


def is_expected_bgutil_owner(
    owner: WindowsPortOwner,
    *,
    server_main: Path = DEFAULT_SERVER_MAIN,
    port: int = DEFAULT_PORT,
) -> bool:
    """Return true only for the repo-local Node provider command on ``port``."""
    process_name = (owner.name or Path(owner.executable_path).name).casefold()
    if process_name not in {"node", "node.exe"}:
        return False

    command = _normalized_command_text(owner.command_line)
    expected_script = _normalized_command_text(str(Path(server_main).resolve()))
    if not expected_script or expected_script not in command:
        return False

    return bool(
        re.search(
            rf"(?:^|\s)--port(?:\s+|=){int(port)}(?:\s|$)",
            command,
            flags=re.IGNORECASE,
        )
    )


def _kill_windows_process_tree(pid: int) -> None:
    try:
        process = subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BgutilOrphanRecoveryError(
            f"Не удалось остановить orphaned bgutil provider PID {pid}"
        ) from exc
    if process.returncode:
        detail = (process.stderr or process.stdout or "").strip()[-700:]
        suffix = f": {detail}" if detail else ""
        raise BgutilOrphanRecoveryError(
            f"Не удалось остановить orphaned bgutil provider PID {pid}" + suffix
        )


def recover_orphaned_bgutil_http_runtime(
    *,
    platform_name: str | None = None,
    server_main: Path = DEFAULT_SERVER_MAIN,
    port: int = DEFAULT_PORT,
) -> int | None:
    """Remove one proven orphaned provider and return its PID.

    This helper is intentionally Windows-only because the managed launcher is a
    Windows BAT and Windows hard termination is the observed orphan path. Other
    platforms retain the existing fail-closed runtime behavior.
    """
    if (platform_name or os.name) != "nt":
        return None

    owners = _probe_windows_port_owners(port)
    if not owners:
        return None
    if len(owners) != 1:
        raise BgutilOrphanRecoveryError(
            f"Порт {port} слушают несколько процессов; автоматическая очистка запрещена"
        )

    owner = owners[0]
    if not is_expected_bgutil_owner(owner, server_main=server_main, port=port):
        raise BgutilOrphanRecoveryError(
            f"Порт {port} занят неизвестным процессом PID {owner.pid}; "
            "автоматическое завершение запрещено"
        )

    _kill_windows_process_tree(owner.pid)
    deadline = time.monotonic() + _STOP_TIMEOUT_SEC
    while time.monotonic() < deadline:
        remaining = _probe_windows_port_owners(port)
        if not remaining:
            return owner.pid
        time.sleep(0.1)

    raise BgutilOrphanRecoveryError(
        f"Orphaned bgutil provider PID {owner.pid} остановлен, но порт {port} не освободился"
    )


__all__ = [
    "BgutilOrphanRecoveryError",
    "DEFAULT_PORT",
    "DEFAULT_SERVER_MAIN",
    "WindowsPortOwner",
    "is_expected_bgutil_owner",
    "recover_orphaned_bgutil_http_runtime",
]
