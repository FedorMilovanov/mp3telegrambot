#!/usr/bin/env python3
"""Pure Gemini quota-domain metadata and error classification.

This module is intentionally stdlib-only and side-effect free so both
``core.globals`` and Factory capacity routing can share one contract without
creating bootstrap/import cycles.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence

GEMINI_QUOTA_DOMAIN_ENVS = (
    "GEMINI_QUOTA_DOMAIN",
    "GEMINI_QUOTA_DOMAIN_2",
    "GEMINI_QUOTA_DOMAIN_3",
    "GEMINI_QUOTA_DOMAIN_4",
)
_SAFE_QUOTA_DOMAIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
_PROJECT_QUOTA_MARKERS = (
    "perproject",
    "projectpermodel",
    "requestsperdayperproject",
    "requestsperminuteperproject",
    "tokensperminuteperproject",
)


def normalize_quota_domain_label(value: object) -> str:
    """Normalize one opaque local label or fail closed on unsafe syntax."""
    text = str(value or "").strip()
    if not text:
        return ""
    if not _SAFE_QUOTA_DOMAIN_RE.fullmatch(text):
        raise RuntimeError(
            "GEMINI_QUOTA_DOMAIN labels must be 1-80 characters using only "
            "letters, digits, dot, underscore, colon or hyphen"
        )
    return text.casefold()


def key_domain_entries(
    keys: Sequence[object],
    *,
    environ: Mapping[str, str] | None = None,
    deduplicate: bool = False,
) -> list[tuple[str, str]]:
    """Return configured key/domain pairs aligned to the four source-owned slots.

    Empty key slots are omitted. Labels are local opaque metadata only; this
    function never derives project identity from credential material.

    When ``deduplicate`` is true, repeated key material collapses to one entry.
    A non-blank label may fill an earlier blank label for the same key, while
    conflicting non-blank labels fail closed.
    """
    source = os.environ if environ is None else environ
    entries: list[tuple[str, str]] = []
    seen: dict[str, int] = {}

    for key_value, env_name in zip(keys, GEMINI_QUOTA_DOMAIN_ENVS):
        key = str(key_value or "").strip()
        if not key:
            continue
        domain = normalize_quota_domain_label(source.get(env_name, ""))

        if not deduplicate:
            entries.append((key, domain))
            continue

        previous_index = seen.get(key)
        if previous_index is None:
            seen[key] = len(entries)
            entries.append((key, domain))
            continue

        previous_key, previous_domain = entries[previous_index]
        if previous_domain and domain and previous_domain != domain:
            raise RuntimeError(
                "Duplicate Gemini API key has conflicting GEMINI_QUOTA_DOMAIN labels"
            )
        if not previous_domain and domain:
            entries[previous_index] = (previous_key, domain)

    return entries


def quota_domains_for_keys(
    keys: Sequence[object],
    *,
    environ: Mapping[str, str] | None = None,
    deduplicate: bool = False,
) -> list[str]:
    """Return normalized quota-domain labels aligned with configured key entries."""
    return [
        domain
        for _key, domain in key_domain_entries(
            keys,
            environ=environ,
            deduplicate=deduplicate,
        )
    ]


def _exception_status_code(exc: BaseException) -> int | None:
    for name in ("code", "status_code", "status"):
        try:
            value = int(getattr(exc, name, None))
        except (TypeError, ValueError):
            continue
        if 100 <= value <= 599:
            return value
    text = str(exc or "").casefold()
    for code in (429, 500, 502, 503, 504):
        if str(code) in text:
            return code
    return None


def is_quota_error(exc: BaseException) -> bool:
    """Classify quota/client-domain exhaustion while excluding known 5xx errors."""
    status_code = _exception_status_code(exc)
    if status_code == 429:
        return True
    if status_code is not None:
        return False
    text = str(exc or "").casefold().replace("_", " ")
    return any(
        marker in text
        for marker in (
            "resource exhausted",
            "quota exceeded",
            "quota exhausted",
            "rate limit exceeded",
        )
    )


def is_project_scoped_quota_error(exc: BaseException) -> bool:
    """Return true only when the quota response explicitly proves project scope."""
    if not is_quota_error(exc):
        return False
    text = str(exc or "").casefold().replace("_", " ").replace("-", " ")
    compact = re.sub(r"[^a-z0-9]+", "", text)
    return any(marker in compact for marker in _PROJECT_QUOTA_MARKERS)


class ProjectQuotaDomainTracker:
    """Request-local suppression for proven project-scoped quota exhaustion.

    The tracker deliberately does not own retry budgets or logging. Unknown clients
    and unlabeled credentials fail open. Independent scopes keep distinct quota
    surfaces, such as Files upload and GenerateContent, from suppressing each other.
    """

    def __init__(self, clients: Sequence[object], domains: Sequence[object]) -> None:
        self._domain_by_client_id: dict[int, str] = {}
        # Runtime startup guarantees positional alignment. If a test/plugin replaces
        # the client list without replacing its metadata, fail open rather than
        # attaching a real domain label to the wrong object.
        if len(clients) != len(domains):
            self._exhausted_by_scope: dict[str, set[str]] = {}
            return
        for client, domain in zip(clients, domains):
            normalized = normalize_quota_domain_label(domain)
            if normalized:
                self._domain_by_client_id[id(client)] = normalized
        self._exhausted_by_scope = {}

    def should_skip(self, client: object, *scopes: str) -> bool:
        domain = self._domain_by_client_id.get(id(client), "")
        if not domain:
            return False
        return any(
            domain in self._exhausted_by_scope.get(scope, set()) for scope in scopes
        )

    def record_project_scoped_error(
        self, client: object, scope: str, exc: BaseException
    ) -> bool:
        domain = self._domain_by_client_id.get(id(client), "")
        if not domain or not is_project_scoped_quota_error(exc):
            return False
        self._exhausted_by_scope.setdefault(scope, set()).add(domain)
        return True


__all__ = [
    "GEMINI_QUOTA_DOMAIN_ENVS",
    "ProjectQuotaDomainTracker",
    "is_project_scoped_quota_error",
    "is_quota_error",
    "key_domain_entries",
    "normalize_quota_domain_label",
    "quota_domains_for_keys",
]
