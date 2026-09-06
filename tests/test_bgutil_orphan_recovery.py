from __future__ import annotations

from pathlib import Path

import pytest

from services import bgutil_orphan_recovery as recovery


def _owner(command_line: str, *, pid: int = 23984, name: str = "node.exe"):
    return recovery.WindowsPortOwner(
        pid=pid,
        name=name,
        executable_path=r"C:\nvm4w\nodejs\node.EXE",
        command_line=command_line,
    )


def test_exact_repo_local_bgutil_owner_is_recognized(tmp_path):
    server_main = tmp_path / "server" / "build" / "main.js"
    command = f'node.exe "{server_main.resolve()}" --port 4416'
    assert recovery.is_expected_bgutil_owner(
        _owner(command),
        server_main=server_main,
        port=4416,
    )


def test_owner_with_other_script_or_port_is_rejected(tmp_path):
    server_main = tmp_path / "server" / "build" / "main.js"
    other_main = tmp_path / "other" / "build" / "main.js"
    assert not recovery.is_expected_bgutil_owner(
        _owner(f'node.exe "{other_main.resolve()}" --port 4416'),
        server_main=server_main,
        port=4416,
    )
    assert not recovery.is_expected_bgutil_owner(
        _owner(f'node.exe "{server_main.resolve()}" --port 9999'),
        server_main=server_main,
        port=4416,
    )


def test_non_node_listener_is_rejected(tmp_path):
    server_main = tmp_path / "server" / "build" / "main.js"
    command = f'python.exe "{server_main.resolve()}" --port 4416'
    assert not recovery.is_expected_bgutil_owner(
        _owner(command, name="python.exe"),
        server_main=server_main,
        port=4416,
    )


def test_non_windows_recovery_is_noop(monkeypatch):
    def unexpected_probe(_port):
        raise AssertionError("non-Windows recovery must not inspect Windows listeners")

    monkeypatch.setattr(recovery, "_probe_windows_port_owners", unexpected_probe)
    assert recovery.recover_orphaned_bgutil_http_runtime(platform_name="posix") is None


def test_windows_recovery_kills_only_proven_repo_local_orphan(monkeypatch, tmp_path):
    server_main = tmp_path / "server" / "build" / "main.js"
    owner = _owner(f'node.exe "{server_main.resolve()}" --port 4416')
    probes = iter([(owner,), ()])
    killed: list[int] = []

    monkeypatch.setattr(
        recovery,
        "_probe_windows_port_owners",
        lambda _port: next(probes),
    )
    monkeypatch.setattr(
        recovery,
        "_kill_windows_process_tree",
        lambda pid: killed.append(pid),
    )

    result = recovery.recover_orphaned_bgutil_http_runtime(
        platform_name="nt",
        server_main=server_main,
        port=4416,
    )

    assert result == owner.pid
    assert killed == [owner.pid]


def test_windows_recovery_refuses_unknown_listener(monkeypatch, tmp_path):
    server_main = tmp_path / "server" / "build" / "main.js"
    stranger = _owner(
        r"node.exe C:\other-project\server\build\main.js --port 4416",
        pid=777,
    )
    monkeypatch.setattr(
        recovery,
        "_probe_windows_port_owners",
        lambda _port: (stranger,),
    )
    monkeypatch.setattr(
        recovery,
        "_kill_windows_process_tree",
        lambda _pid: pytest.fail("unknown listener must never be killed"),
    )

    with pytest.raises(recovery.BgutilOrphanRecoveryError, match="неизвестным процессом"):
        recovery.recover_orphaned_bgutil_http_runtime(
            platform_name="nt",
            server_main=server_main,
            port=4416,
        )


def test_bot_entrypoint_acquires_singleton_before_bgutil_runtime():
    root = Path(__file__).resolve().parents[1]
    source = (root / "bot_new.py").read_text(encoding="utf-8")

    singleton_index = source.index("if not acquire_early_singleton():")
    recovery_index = source.index("recover_orphaned_bgutil_http_runtime()")
    provision_index = source.index("ensure_bgutil_provider()")
    runtime_index = source.index("require_youtube_po_token_runtime()")
    bootstrap_index = source.index("bootstrap_pre_main()")

    assert singleton_index < recovery_index < provision_index < runtime_index < bootstrap_index
