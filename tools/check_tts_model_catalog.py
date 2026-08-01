#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate declarative TTS model manifests and adapter conformance."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


def _configure_stdio() -> None:
    """Make service import diagnostics safe on Windows legacy consoles."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, OSError):
            pass


_configure_stdio()
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.speech_backends import (  # noqa: E402
    CONFIGURED_DEFAULT_MODEL_PROFILE_ID,
    ProfileManifestError,
    catalog_snapshot,
    default_profile_manifest_root,
    get_backend_model_contract,
    load_profile_catalog,
)


def _canonical_default(records: tuple[Any, ...], requested: str) -> str:
    value = str(requested or "").strip().casefold().replace("_", "-")
    matches: list[str] = []
    for record in records:
        profile = record.profile
        if value in (profile.profile_id, *profile.aliases):
            matches.append(profile.profile_id)
    if len(matches) != 1:
        available = ", ".join(record.profile.profile_id for record in records) or "—"
        raise ProfileManifestError(
            f"Default TTS profile {requested!r} не разрешён однозначно. "
            f"Доступно: {available}."
        )
    return matches[0]


def validate_catalog(
    root: Path,
    *,
    default_profile: str,
) -> dict[str, Any]:
    records = load_profile_catalog(root)
    for record in records:
        contract = get_backend_model_contract(record.profile.backend_id)
        contract.validate_profile(record.profile)
    canonical_default = _canonical_default(records, default_profile)
    selected = next(
        record.profile for record in records if record.profile.profile_id == canonical_default
    )
    if not selected.production_enabled:
        raise ProfileManifestError(
            f"Default TTS profile отключён для production: {canonical_default}"
        )
    snapshot = catalog_snapshot(records, root=root)
    snapshot["configured_default"] = str(default_profile)
    snapshot["canonical_default"] = canonical_default
    snapshot["adapter_contracts"] = {
        record.profile.backend_id: get_backend_model_contract(
            record.profile.backend_id
        ).as_dict()
        for record in records
    }
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate repository-owned TTS model profile manifests."
    )
    parser.add_argument(
        "--root",
        default=str(default_profile_manifest_root()),
        help="catalog directory containing direct-child JSON manifests",
    )
    parser.add_argument(
        "--default-profile",
        default=CONFIGURED_DEFAULT_MODEL_PROFILE_ID,
        help="profile id or alias selected as the production default",
    )
    parser.add_argument("--json", action="store_true", help="print JSON snapshot")
    args = parser.parse_args(argv)
    try:
        snapshot = validate_catalog(
            Path(args.root).resolve(),
            default_profile=args.default_profile,
        )
    except Exception as exc:
        print(f"TTS_MODEL_CATALOG_FAILED: {type(exc).__name__}: {exc}")
        return 1
    if args.json:
        print(
            json.dumps(
                snapshot,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
        )
    else:
        print(
            "TTS_MODEL_CATALOG_OK "
            f"profiles={snapshot['profile_count']} "
            f"default={snapshot['canonical_default']} "
            f"policy={snapshot['policy']}"
        )
        for item in snapshot["profiles"]:
            profile = item["profile"]
            print(
                "- "
                f"{profile['profile_id']} backend={profile['backend_id']} "
                f"revision={profile['model_revision']} "
                f"manifest_sha256={item['source_sha256']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
