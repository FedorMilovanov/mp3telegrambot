#!/usr/bin/env python3
"""Single source of truth for the pinned bgutil PO-token provider.

Why this module exists
----------------------
The exact upstream identity of the browserless YouTube PO-token provider used to
be duplicated in ``tools/ensure_bgutil_provider.py`` (provisioning) and
``services/youtube_po_token_runtime.py`` (readiness gate).  A version bump had
to touch both, plus the docs, and any drift showed up only at bot startup.

Now the pin lives here and everything else imports it.

Security contract
-----------------
``GHSA-qpv9-8xfj-xx9m`` (published 2026-09-08, RCE) affects every release
**below 2.0.0**: the server bound ``::`` and fell back to ``0.0.0.0``, so any
device on the same network — or a browser page on an unpatched browser — could
POST a crafted ``/get_pot`` request and execute code.  ``2.0.0`` binds loopback
only, rejects browser-originated requests and validates proxy schemes.

This deployment therefore requires:

1. version >= ``BGUTIL_FIRST_PATCHED_VERSION`` (enforced by
   :func:`bgutil_pin_problems`, checked at provisioning time);
2. an explicit ``--host 127.0.0.1`` on the server command line, so the binding
   is a property of *our* argv and not only of upstream defaults;
3. the yt-dlp route pointing at ``http://127.0.0.1:4416`` (see ``yt-dlp.conf``).

The module is dependency-free: it is imported by provisioning tooling that runs
before the rest of the project is importable.
"""
from __future__ import annotations

from typing import Final

# ── Upstream pin ─────────────────────────────────────────────────────────────
BGUTIL_VERSION: Final[str] = "2.0.2"
BGUTIL_COMMIT: Final[str] = "26475e9d3665b972c4aeb83b4dbaafcf64b88438"
BGUTIL_REPOSITORY: Final[str] = (
    "https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git"
)

# ── Runtime requirements ────────────────────────────────────────────────────
# 2.0.x server: "engines": {"node": ">=22", "npm": ">=9"}.
BGUTIL_MIN_NODE_MAJOR: Final[int] = 22
BGUTIL_MIN_NPM_MAJOR: Final[int] = 9

# ── Loopback HTTP contract ──────────────────────────────────────────────────
BGUTIL_HTTP_HOST: Final[str] = "127.0.0.1"
BGUTIL_HTTP_PORT: Final[int] = 4416
BGUTIL_HTTP_BASE_URL: Final[str] = f"http://{BGUTIL_HTTP_HOST}:{BGUTIL_HTTP_PORT}"

# ── Security baseline ───────────────────────────────────────────────────────
BGUTIL_SECURITY_ADVISORY: Final[str] = "GHSA-qpv9-8xfj-xx9m"
BGUTIL_FIRST_PATCHED_VERSION: Final[str] = "2.0.0"

_HEX_DIGITS: Final[frozenset[str]] = frozenset("0123456789abcdef")


def _is_commit_sha(value: str) -> bool:
    """A full 40-character lowercase hex sha."""
    return len(value) == 40 and all(char in _HEX_DIGITS for char in value)


def _leading_digits(chunk: str) -> str:
    for index, char in enumerate(chunk):
        if not char.isdigit():
            return chunk[:index]
    return chunk


def version_tuple(value: str) -> tuple[int, ...]:
    """Parse ``"2.0.2"`` into ``(2, 0, 2)``; non-numeric parts are ignored."""
    parts: list[int] = []
    for chunk in str(value or "").strip().split("."):
        digits = _leading_digits(chunk)
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def pinned_version() -> tuple[int, ...]:
    return version_tuple(BGUTIL_VERSION)


def patched_version() -> tuple[int, ...]:
    return version_tuple(BGUTIL_FIRST_PATCHED_VERSION)


def pin_is_patched() -> bool:
    """``True`` when the pinned release contains the RCE fix."""
    return pinned_version() >= patched_version()


def marker_text() -> str:
    """Identity written to ``.mp3bot-bgutil-version`` and compared at startup."""
    return f"{BGUTIL_VERSION}@{BGUTIL_COMMIT}"


def bgutil_pin_problems() -> list[str]:
    """Configuration problems that must fail before anything is provisioned."""
    problems: list[str] = []
    if not pin_is_patched():
        problems.append(
            f"pinned bgutil {BGUTIL_VERSION} is below {BGUTIL_FIRST_PATCHED_VERSION}: "
            f"remote code execution {BGUTIL_SECURITY_ADVISORY} is not fixed"
        )
    if not _is_commit_sha(BGUTIL_COMMIT):
        problems.append(
            f"BGUTIL_COMMIT must be a full 40-hex commit sha, got {BGUTIL_COMMIT!r}"
        )
    if BGUTIL_HTTP_HOST not in {"127.0.0.1", "::1", "localhost"}:
        problems.append(
            f"bgutil HTTP host must stay loopback, got {BGUTIL_HTTP_HOST!r}"
        )
    if BGUTIL_MIN_NODE_MAJOR < 22:
        problems.append(
            f"bgutil {BGUTIL_VERSION} requires Node >= 22, floor is "
            f"{BGUTIL_MIN_NODE_MAJOR}"
        )
    return problems


__all__ = [
    "BGUTIL_COMMIT",
    "BGUTIL_FIRST_PATCHED_VERSION",
    "BGUTIL_HTTP_BASE_URL",
    "BGUTIL_HTTP_HOST",
    "BGUTIL_HTTP_PORT",
    "BGUTIL_MIN_NODE_MAJOR",
    "BGUTIL_MIN_NPM_MAJOR",
    "BGUTIL_REPOSITORY",
    "BGUTIL_SECURITY_ADVISORY",
    "BGUTIL_VERSION",
    "bgutil_pin_problems",
    "marker_text",
    "patched_version",
    "pin_is_patched",
    "pinned_version",
    "version_tuple",
]
