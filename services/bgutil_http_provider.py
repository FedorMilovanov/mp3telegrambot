#!/usr/bin/env python3
"""Own the repo-local bgutil HTTP provider used by production yt-dlp.

The token engine is the exact pinned upstream build. A tiny repo-owned Node
transport exposes it only on loopback, avoiding upstream 1.3.1's all-interface
HTTP listener and avoiding the script provider's per-request version preflight.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from services.youtube_po_token_runtime import (
    BGUTIL_EXPECTED_COMMIT,
    BGUTIL_EXPECTED_VERSION,
    DEFAULT_PROVIDER_ROOT,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 4417
HTTP_BASE_URL = f"http://{HTTP_HOST}:{HTTP_PORT}"
HTTP_OWNER_POLICY = "mp3telegrambot-bgutil-loopback-v1"
HTTP_WRAPPER = PROJECT_ROOT / "tools" / "bgutil_http_loopback.mjs"
HTTP_LOG = PROJECT_ROOT / ".runtime" / "bgutil-http-provider.log"
HTTP_SESSION_MANAGER = DEFAULT_PROVIDER_ROOT / "server" / "build" / "session_manager.js"
HTTP_UTILS = DEFAULT_PROVIDER_ROOT / "server" / "build" / "utils.js"
HTTP_MARKER = DEFAULT_PROVIDER_ROOT / ".mp3bot-bgutil-version"
EXPECTED_PROVIDER_MARKER = f"{BGUTIL_EXPECTED_VERSION}@{BGUTIL_EXPECTED_COMMIT}"
_START_TIMEOUT_SEC = 20.0
_PROBE_TIMEOUT_SEC = 1.0
_STOP_TIMEOUT_SEC = 5.0


class BgutilHttpProviderError(RuntimeError):
    """Raised when the loopback bgutil HTTP provider cannot be proven ready."""


def _tail(path: Path, limit: int = 1800) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-limit:].decode("utf-8", errors="replace").strip()


def _probe_ping() -> dict[str, Any] | None:
    request = urllib.request.Request(
        f"{HTTP_BASE_URL}/ping",
        headers={"Connection": "close", "User-Agent": "mp3telegrambot-bgutil-probe/1"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=_PROBE_TIMEOUT_SEC) as response:
            status = int(getattr(response, "status", 200) or 200)
            payload = response.read(64 * 1024)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return None
    if status != 200:
        raise BgutilHttpProviderError(
            f"Unexpected HTTP status from {HTTP_BASE_URL}/ping: {status}"
        )
    try:
        decoded = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BgutilHttpProviderError(
            f"Port {HTTP_PORT} responds, but /ping is not valid bgutil JSON"
        ) from exc
    if not isinstance(decoded, dict):
        raise BgutilHttpProviderError(
            f"Port {HTTP_PORT} responds, but /ping is not a JSON object"
        )
    return decoded


def _require_exact_ping(payload: dict[str, Any]) -> None:
    version = str(payload.get("version") or "").strip()
    owner = str(payload.get("owner") or "").strip()
    marker = str(payload.get("provider_marker") or "").strip()
    if version != BGUTIL_EXPECTED_VERSION:
        raise BgutilHttpProviderError(
            "bgutil HTTP provider version mismatch: "
            f"expected={BGUTIL_EXPECTED_VERSION} actual={version or 'unknown'}"
        )
    if owner != HTTP_OWNER_POLICY:
        raise BgutilHttpProviderError(
            f"Port {HTTP_PORT} is occupied by an unowned/foreign HTTP service: "
            f"owner={owner or 'missing'}"
        )
    if marker != EXPECTED_PROVIDER_MARKER:
        raise BgutilHttpProviderError(
            "bgutil HTTP provider source marker mismatch: "
            f"expected={EXPECTED_PROVIDER_MARKER} actual={marker or 'missing'}"
        )


def _require_runtime_files() -> None:
    missing = [
        str(path.relative_to(PROJECT_ROOT))
        for path in (HTTP_WRAPPER, HTTP_SESSION_MANAGER, HTTP_UTILS, HTTP_MARKER)
        if not path.is_file()
    ]
    if missing:
        raise BgutilHttpProviderError(
            "bgutil HTTP runtime is incomplete after provisioning: " + ", ".join(missing)
        )
    try:
        marker = HTTP_MARKER.read_text(encoding="utf-8", errors="strict").strip()
    except (OSError, UnicodeError) as exc:
        raise BgutilHttpProviderError("Cannot read bgutil provider marker") from exc
    if marker != EXPECTED_PROVIDER_MARKER:
        raise BgutilHttpProviderError(
            "bgutil provider marker drift before HTTP start: "
            f"expected={EXPECTED_PROVIDER_MARKER} actual={marker or 'missing'}"
        )


def _require_node() -> str:
    node = shutil.which("node")
    if not node:
        raise BgutilHttpProviderError("Node.js not found for bgutil HTTP provider")
    return node


@dataclass
class BgutilHttpProviderSession:
    """One provider ownership handle; safe to close repeatedly."""

    process: subprocess.Popen[bytes] | None
    log_stream: IO[bytes] | None
    owned: bool
    _closed: bool = False

    @property
    def status_text(self) -> str:
        return f"http={HTTP_BASE_URL}; owned={'yes' if self.owned else 'reused'}"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self.process
        try:
            if self.owned and process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=_STOP_TIMEOUT_SEC)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=_STOP_TIMEOUT_SEC)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("bgutil HTTP provider cleanup failed: %s", exc)
        finally:
            if self.log_stream is not None:
                try:
                    self.log_stream.close()
                except OSError:
                    pass
                self.log_stream = None


def start_bgutil_http_provider() -> BgutilHttpProviderSession:
    """Start or reuse only the exact repo-owned loopback provider, fail closed."""
    _require_runtime_files()

    existing = _probe_ping()
    if existing is not None:
        _require_exact_ping(existing)
        return BgutilHttpProviderSession(process=None, log_stream=None, owned=False)

    node = _require_node()
    HTTP_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        log_stream = HTTP_LOG.open("ab", buffering=0)
    except OSError as exc:
        raise BgutilHttpProviderError(
            f"Cannot open bgutil HTTP provider log: {HTTP_LOG}"
        ) from exc

    command = [node, str(HTTP_WRAPPER)]
    try:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
    except OSError as exc:
        log_stream.close()
        raise BgutilHttpProviderError("Cannot start bgutil loopback provider") from exc

    session = BgutilHttpProviderSession(
        process=process,
        log_stream=log_stream,
        owned=True,
    )
    deadline = time.monotonic() + _START_TIMEOUT_SEC
    try:
        while time.monotonic() < deadline:
            returncode = process.poll()
            if returncode is not None:
                detail = _tail(HTTP_LOG)
                suffix = f" Detail: {detail}" if detail else ""
                raise BgutilHttpProviderError(
                    f"bgutil loopback provider exited during startup ({returncode}).{suffix}"
                )
            payload = _probe_ping()
            if payload is not None:
                _require_exact_ping(payload)
                logger.info(
                    "bgutil HTTP provider ready: %s marker=%s",
                    HTTP_BASE_URL,
                    EXPECTED_PROVIDER_MARKER,
                )
                return session
            time.sleep(0.2)
    except BaseException:
        session.close()
        raise

    session.close()
    detail = _tail(HTTP_LOG)
    suffix = f" Detail: {detail}" if detail else ""
    raise BgutilHttpProviderError(
        f"bgutil loopback provider did not become ready in {_START_TIMEOUT_SEC:g}s.{suffix}"
    )


__all__ = [
    "BgutilHttpProviderError",
    "BgutilHttpProviderSession",
    "EXPECTED_PROVIDER_MARKER",
    "HTTP_BASE_URL",
    "HTTP_HOST",
    "HTTP_OWNER_POLICY",
    "HTTP_PORT",
    "HTTP_WRAPPER",
    "start_bgutil_http_provider",
]
