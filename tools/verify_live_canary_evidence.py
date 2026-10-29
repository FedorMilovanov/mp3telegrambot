#!/usr/bin/env python3
"""Validate the structure of sanitized live-canary evidence without network access.

This tool deliberately validates completeness, not the authenticity of provider
calls. A reviewer must also inspect the sanitized proof artifacts identified by
their SHA-256 digests. No credentials, raw logs, URLs, paths, or media belong in
the evidence JSON.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from typing import cast

SCHEMA = "mp3bot-live-canary-evidence-v1"
REPOSITORY = "FedorMilovanov/mp3telegrambot"
SANITIZATION_POLICY = "sanitized-no-secrets-no-user-media-v1"
REQUIRED_BOUNDARIES = (
    "telegram",
    "local_bot_api",
    "download",
    "conversion",
    "gemini",
    "telegraph",
    "livedub",
    "delivery",
    "restart_recovery",
)
SOURCE_KINDS = frozenset({"operator_canary", "production_telemetry"})
MIN_LARGE_MEDIA_BYTES = 50 * 1024 * 1024
MAX_LARGE_MEDIA_BYTES = 2000 * 1024 * 1024
MAX_EVIDENCE_BYTES = 64 * 1024
_HEX_RE = re.compile(r"[0-9a-fA-F]+")


class LiveCanaryEvidenceError(ValueError):
    """Evidence is malformed, incomplete, unsafe, or bound to another source."""


def _object(value: object, label: str, keys: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise LiveCanaryEvidenceError(f"{label} must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise LiveCanaryEvidenceError(f"{label} must contain only string keys")
    result = cast(dict[str, object], value)
    actual = set(result)
    missing = sorted(keys - actual)
    unknown = sorted(actual - keys)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing={','.join(missing)}")
        if unknown:
            details.append(f"unknown={','.join(unknown)}")
        raise LiveCanaryEvidenceError(f"{label} has invalid keys ({'; '.join(details)})")
    return result


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LiveCanaryEvidenceError(f"{label} must be a non-empty string")
    return value.strip()


def _choice(value: object, label: str, allowed: frozenset[str]) -> str:
    text = _text(value, label)
    if text not in allowed:
        raise LiveCanaryEvidenceError(f"{label} must be one of {','.join(sorted(allowed))}")
    return text


def _hex(value: object, *, length: int, label: str) -> str:
    text = _text(value, label).casefold()
    if len(text) != length or _HEX_RE.fullmatch(text) is None:
        raise LiveCanaryEvidenceError(f"{label} must be exactly {length} hexadecimal characters")
    return text


def _timestamp(value: object, label: str) -> datetime:
    text = _text(value, label)
    normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise LiveCanaryEvidenceError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LiveCanaryEvidenceError(f"{label} must include a timezone")
    return parsed


def _within_window(observed: datetime, started: datetime, completed: datetime, label: str) -> None:
    if not started <= observed <= completed:
        raise LiveCanaryEvidenceError(f"{label} must be inside the canary start/completion window")


def _proof(payload: dict[str, object], started: datetime, completed: datetime, label: str) -> None:
    _choice(payload["result"], f"{label}.result", frozenset({"passed"}))
    observed = _timestamp(payload["observed_at"], f"{label}.observed_at")
    _within_window(observed, started, completed, f"{label}.observed_at")
    _hex(payload["proof_sha256"], length=64, label=f"{label}.proof_sha256")


def build_evidence_template(expected_sha: str) -> dict[str, object]:
    """Return an intentionally incomplete template which cannot validate as evidence."""
    commit_sha = _hex(expected_sha, length=40, label="expected_sha")
    return {
        "schema": SCHEMA,
        "repository": REPOSITORY,
        "commit_sha": commit_sha,
        "source_kind": "operator_canary",
        "started_at": "",
        "completed_at": "",
        "runtime": {"build_sha": commit_sha, "dirty": "unknown"},
        "boundaries": [
            {"id": boundary, "result": "pending", "observed_at": "", "proof_sha256": ""}
            for boundary in REQUIRED_BOUNDARIES
        ],
        "large_media": {
            "result": "pending",
            "observed_at": "",
            "size_bytes": 0,
            "delivery_route": "unverified",
            "proof_sha256": "",
        },
        "sanitization": {
            "policy": SANITIZATION_POLICY,
            "secrets_removed": False,
            "user_media_removed": False,
            "identifiers_removed": False,
        },
    }


def validate_evidence(payload: object, *, expected_sha: str) -> None:
    """Fail closed unless every required live boundary has sanitized proof."""
    expected = _hex(expected_sha, length=40, label="expected_sha")
    root = _object(
        payload,
        "evidence",
        frozenset(
            {
                "schema",
                "repository",
                "commit_sha",
                "source_kind",
                "started_at",
                "completed_at",
                "runtime",
                "boundaries",
                "large_media",
                "sanitization",
            }
        ),
    )
    if _text(root["schema"], "evidence.schema") != SCHEMA:
        raise LiveCanaryEvidenceError(f"evidence.schema must be {SCHEMA}")
    if _text(root["repository"], "evidence.repository") != REPOSITORY:
        raise LiveCanaryEvidenceError(f"evidence.repository must be {REPOSITORY}")
    if _hex(root["commit_sha"], length=40, label="evidence.commit_sha") != expected:
        raise LiveCanaryEvidenceError("evidence.commit_sha does not match the required source SHA")
    _choice(root["source_kind"], "evidence.source_kind", SOURCE_KINDS)

    started = _timestamp(root["started_at"], "evidence.started_at")
    completed = _timestamp(root["completed_at"], "evidence.completed_at")
    if completed < started:
        raise LiveCanaryEvidenceError("evidence.completed_at must not precede started_at")

    runtime = _object(root["runtime"], "evidence.runtime", frozenset({"build_sha", "dirty"}))
    if _hex(runtime["build_sha"], length=40, label="evidence.runtime.build_sha") != expected:
        raise LiveCanaryEvidenceError("runtime build SHA does not match the required source SHA")
    if _choice(runtime["dirty"], "evidence.runtime.dirty", frozenset({"yes", "no", "unknown"})) != "no":
        raise LiveCanaryEvidenceError("runtime source tree must be clean at the exact required SHA")

    raw_boundaries = root["boundaries"]
    if not isinstance(raw_boundaries, list):
        raise LiveCanaryEvidenceError("evidence.boundaries must be a JSON array")
    seen: set[str] = set()
    for index, raw_boundary in enumerate(cast(list[object], raw_boundaries)):
        label = f"evidence.boundaries[{index}]"
        boundary = _object(
            raw_boundary,
            label,
            frozenset({"id", "result", "observed_at", "proof_sha256"}),
        )
        boundary_id = _choice(boundary["id"], f"{label}.id", frozenset(REQUIRED_BOUNDARIES))
        if boundary_id in seen:
            raise LiveCanaryEvidenceError(f"duplicate live boundary: {boundary_id}")
        seen.add(boundary_id)
        _proof(boundary, started, completed, label)
    missing = sorted(set(REQUIRED_BOUNDARIES) - seen)
    if missing:
        raise LiveCanaryEvidenceError(f"missing live boundaries: {','.join(missing)}")

    large_media = _object(
        root["large_media"],
        "evidence.large_media",
        frozenset({"result", "observed_at", "size_bytes", "delivery_route", "proof_sha256"}),
    )
    _proof(large_media, started, completed, "evidence.large_media")
    size = large_media["size_bytes"]
    if isinstance(size, bool) or not isinstance(size, int):
        raise LiveCanaryEvidenceError("evidence.large_media.size_bytes must be an integer")
    if not MIN_LARGE_MEDIA_BYTES < size <= MAX_LARGE_MEDIA_BYTES:
        raise LiveCanaryEvidenceError(
            "large media must be larger than 50 MiB and no larger than the supported 2000 MiB"
        )
    _choice(
        large_media["delivery_route"],
        "evidence.large_media.delivery_route",
        frozenset({"local_bot_api"}),
    )

    sanitization = _object(
        root["sanitization"],
        "evidence.sanitization",
        frozenset({"policy", "secrets_removed", "user_media_removed", "identifiers_removed"}),
    )
    if _text(sanitization["policy"], "evidence.sanitization.policy") != SANITIZATION_POLICY:
        raise LiveCanaryEvidenceError("unsupported sanitization policy")
    for key in ("secrets_removed", "user_media_removed", "identifiers_removed"):
        if sanitization[key] is not True:
            raise LiveCanaryEvidenceError(f"evidence.sanitization.{key} must be true")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LiveCanaryEvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> object:
    raise LiveCanaryEvidenceError(f"non-finite JSON number is forbidden: {value}")


def load_evidence(path: Path) -> object:
    """Read a bounded UTF-8 JSON document and reject ambiguous duplicate keys."""
    try:
        if not path.is_file() or not 1 <= path.stat().st_size <= MAX_EVIDENCE_BYTES:
            raise LiveCanaryEvidenceError("evidence file must be non-empty and no larger than 64 KiB")
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise LiveCanaryEvidenceError(f"cannot read evidence file as UTF-8: {exc}") from exc
    try:
        return cast(
            object,
            json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant),
        )
    except json.JSONDecodeError as exc:
        raise LiveCanaryEvidenceError(f"invalid evidence JSON: line {exc.lineno} column {exc.colno}") from exc
    except RecursionError as exc:
        raise LiveCanaryEvidenceError("evidence JSON is nested too deeply") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("evidence", nargs="?", type=Path, help="Sanitized evidence JSON to validate")
    parser.add_argument("--expected-sha", required=True, help="Required full 40-character source commit")
    parser.add_argument("--template", action="store_true", help="Print an intentionally incomplete JSON template")
    args = parser.parse_args(argv)
    if args.template and args.evidence is not None:
        parser.error("--template cannot be combined with an evidence path")
    if not args.template and args.evidence is None:
        parser.error("an evidence path is required unless --template is used")

    try:
        if args.template:
            print(json.dumps(build_evidence_template(args.expected_sha), ensure_ascii=False, indent=2))
            return 0
        validate_evidence(load_evidence(args.evidence), expected_sha=args.expected_sha)
    except LiveCanaryEvidenceError as exc:
        print(f"live canary evidence rejected: {exc}", file=sys.stderr)
        return 1

    print(
        "live canary evidence structure OK for "
        f"{args.expected_sha.strip().casefold()}; sanitized proof artifacts still require independent review"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
