#!/usr/bin/env python3
"""Remove ambient proxy state and legacy rebinding from Local Bot API runtime."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "services" / "local_botapi_runtime.py"
REQUIRED = ROOT / "services" / "local_botapi_required.py"


def replace_function(text: str, path: Path, name: str, source: str) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            lines[node.lineno - 1 : (node.end_lineno or node.lineno)] = [source.rstrip() + "\n"]
            return "".join(lines)
    raise RuntimeError(f"{path}: function {name} not found")


def remove_function(text: str, path: Path, name: str) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            del lines[start : (node.end_lineno or node.lineno)]
            return "".join(lines)
    raise RuntimeError(f"{path}: function {name} not found")


PROXY_ARGS = '''def _proxy_args(proxy_url: str = "") -> list[str]:
    proxy = str(proxy_url or "").strip()
    if not proxy:
        return []
    parsed = urlparse(proxy)
    if (parsed.scheme or "").lower() in {"http", "https"} and parsed.hostname:
        return [f"--proxy={proxy}"]
    return []
'''

START_SERVER = '''def _start_server(host: str, port: int, *, proxy_url: str = ""):
    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    exe = os.getenv(
        "LOCAL_BOT_API_EXE",
        r"C:\\Program Files\\TelegramBotAPI\\telegram-bot-api.exe",
    ).strip()
    if not api_id or not api_hash:
        return None, "TELEGRAM_API_ID/TELEGRAM_API_HASH are missing"
    if not Path(exe).is_file():
        return None, f"telegram-bot-api executable not found: {exe}"

    try:
        data_dir = _writable_data_dir()
    except Exception as exc:
        return None, str(exc)
    log_path = data_dir.parent / "botapi-server.log"
    command = [
        exe,
        f"--api-id={api_id}",
        f"--api-hash={api_hash}",
        "--local",
        f"--http-port={port}",
        f"--dir={data_dir}",
        f"--log={log_path}",
        "--verbosity=2",
        *_proxy_args(proxy_url),
    ]
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "env": dict(os.environ),
    }
    handle = None
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", errors="replace") as marker:
            marker.write(
                f"\\n===== runtime bootstrap {time.strftime('%Y-%m-%d %H:%M:%S')} =====\\n"
            )
        handle = log_path.open("ab")
        kwargs["stdout"] = handle
        kwargs["stderr"] = subprocess.STDOUT
        if os.name == "nt":
            kwargs["creationflags"] = 0x8 | 0x200 | 0x08000000
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(command, **kwargs)  # type: ignore[arg-type]
        _write_pid(_pid_path(data_dir), process.pid)
        return process, str(log_path)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        if handle is not None:
            handle.close()
'''

REDACT = '''def _redact(text: str, *, proxy_url: str = "") -> str:
    result = str(text or "")
    secrets = [
        os.getenv("BOT_TOKEN", "").strip(),
        os.getenv("TELEGRAM_API_HASH", "").strip(),
        os.getenv("LOCAL_BOT_API_PROXY_PASSWORD", "").strip(),
    ]
    proxy = str(proxy_url or "").strip()
    if proxy:
        password = urlparse(proxy).password
        if password:
            secrets.append(password)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "***")
    return re.sub(r"/bot\\d+:[A-Za-z0-9_-]+", "/bot***", result)
'''

READ_TAIL = '''def _read_log_tail(
    path_text: str,
    max_chars: int = 2200,
    *,
    proxy_url: str = "",
) -> str:
    try:
        path = Path(path_text)
        if not path.is_file():
            return ""
        return _redact(
            path.read_text(encoding="utf-8", errors="replace")[-max_chars:].strip(),
            proxy_url=proxy_url,
        )
    except OSError:
        return ""
'''

FAILURE_REASON = '''def _failure_reason(
    detail: str,
    log_path: str | Path,
    *,
    proxy_url: str = "",
) -> str:
    tail = process_runtime._read_log_tail(
        str(log_path),
        max_chars=1600,
        proxy_url=proxy_url,
    )
    tail_line = " | ".join(line.strip() for line in tail.splitlines()[-8:] if line.strip())
    reason = f"локальный /getMe не поднялся: {detail}"
    if tail_line:
        reason += f"; botapi-server.log: {tail_line[:900]}"
    reason += "; сервер оставлен запущенным — включи/исправь системный TUN и запусти бот повторно"
    return reason
'''


def main() -> int:
    runtime = RUNTIME.read_text(encoding="utf-8")
    runtime = runtime.replace(
        '"""Windows-safe runtime hardening for :mod:`local_botapi_bootstrap`.\n\n'
        'The original bootstrap owns the route/getMe policy. This adapter narrows the\n'
        'process-management surface and makes documented configuration real:\n',
        '"""Source-owned Local Bot API process management.\n\n'
        'The mandatory startup policy composes these primitives explicitly. They provide:\n',
        1,
    )
    runtime = runtime.replace(
        '* BOT_TOKEN/API hash/proxy passwords are redacted from diagnostic log tails.\n"""',
        '* BOT_TOKEN/API hash/proxy passwords are redacted from diagnostic log tails.\n\n'
        'No imported bootstrap function is replaced at runtime and no proxy state is ambient.\n"""',
        1,
    )
    runtime = runtime.replace("from services import local_botapi_bootstrap as legacy\n\n", "")
    runtime = runtime.replace('_ACTIVE_PROXY_URL = ""\n', "")
    runtime = replace_function(runtime, RUNTIME, "_proxy_args", PROXY_ARGS)
    runtime = replace_function(runtime, RUNTIME, "_start_server", START_SERVER)
    runtime = replace_function(runtime, RUNTIME, "_redact", REDACT)
    runtime = replace_function(runtime, RUNTIME, "_read_log_tail", READ_TAIL)
    runtime = remove_function(runtime, RUNTIME, "_cloud_available")
    runtime = remove_function(runtime, RUNTIME, "prepare_local_bot_api")

    forbidden_runtime = (
        "_ACTIVE_PROXY_URL",
        "legacy._terminate_stale_server =",
        "legacy._start_local_server =",
        "legacy._read_log_tail =",
        "global _ACTIVE_PROXY_URL",
    )
    bad = [token for token in forbidden_runtime if token in runtime]
    if bad:
        raise RuntimeError(f"Local Bot API runtime mutation survived: {bad}")
    ast.parse(runtime, filename=str(RUNTIME))
    RUNTIME.write_text(runtime, encoding="utf-8")

    required = REQUIRED.read_text(encoding="utf-8")
    required = replace_function(required, REQUIRED, "_failure_reason", FAILURE_REASON)
    old = '''    process_runtime._ACTIVE_PROXY_URL = os.getenv("LOCAL_BOT_API_PROXY_URL", "").strip()\n    process_runtime._terminate_managed_server()\n    probe_runtime._wait_until_port_closes(host, port, time.monotonic() + 3.0)\n\n    process, log_path = process_runtime._start_server(host, port)\n'''
    new = '''    proxy_url = os.getenv("LOCAL_BOT_API_PROXY_URL", "").strip()\n    process_runtime._terminate_managed_server()\n    probe_runtime._wait_until_port_closes(host, port, time.monotonic() + 3.0)\n\n    process, log_path = process_runtime._start_server(\n        host,\n        port,\n        proxy_url=proxy_url,\n    )\n'''
    if old not in required:
        raise RuntimeError("required Local Bot API ambient proxy block not found")
    required = required.replace(old, new, 1)
    required = required.replace(
        "_failure_reason(detail, _log_path())",
        "_failure_reason(detail, _log_path(), proxy_url=os.getenv(\"LOCAL_BOT_API_PROXY_URL\", \"\").strip())",
    )
    required = required.replace(
        "_failure_reason(detail, log_path)",
        "_failure_reason(detail, log_path, proxy_url=proxy_url)",
    )
    if "process_runtime._ACTIVE_PROXY_URL" in required:
        raise RuntimeError("required Local Bot API still mutates runtime proxy state")
    ast.parse(required, filename=str(REQUIRED))
    REQUIRED.write_text(required, encoding="utf-8")
    print("Local Bot API process policy is source-owned with explicit proxy configuration")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
