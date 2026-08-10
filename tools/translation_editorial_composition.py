#!/usr/bin/env python3
"""PowerShell-friendly CLI for reviewed translation composition."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.media_delivery_probe import media_probe_is_deliverable, probe_media_async
from services.translation_editorial import sha256_file, validate_review_document
from services.translation_editorial_composition import (
    build_composition_template,
    build_release_handoff,
    refresh_composition_id,
    render_composition,
    validate_composition_document,
)
from services.translation_editorial_pack_contract import load_verified_review_pack
from services.translation_editorial_repair_provenance import verify_repair_provenance


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def _write_atomic(path: Path, data: dict[str, Any], *, overwrite: bool) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite and path.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {path}")
    payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        temp.write_text(payload, encoding="utf-8")
        if overwrite:
            os.replace(temp, path)
        else:
            try:
                os.link(temp, path)
            except OSError:
                try:
                    with path.open("x", encoding="utf-8") as stream:
                        stream.write(payload)
                except Exception:
                    path.unlink(missing_ok=True)
                    raise
    finally:
        temp.unlink(missing_ok=True)


def _write_handoff(path: Path, data: dict[str, Any]) -> None:
    path = Path(path)
    if path.exists():
        existing = _load(path)
        if existing.get("handoff_id") == data.get("handoff_id") and existing == data:
            return
        raise FileExistsError(
            f"existing handoff belongs to different rendered state; refusing overwrite: {path}"
        )
    _write_atomic(path, data, overwrite=False)


def _review_binding(args: argparse.Namespace) -> tuple[str, str]:
    review_path = Path(args.review) if args.review else None
    pack_path = Path(args.review_pack) if args.review_pack else None
    manual_pack_id = str(args.review_pack_id or "").strip()
    if pack_path is not None and review_path is None:
        raise ValueError("--review-pack requires --review")
    if review_path is None:
        return manual_pack_id, ""

    review = _load(review_path)
    review_id = str(review.get("review_pack_id") or "").strip()
    if not review_id:
        raise ValueError("review.json does not contain review_pack_id")
    if manual_pack_id and manual_pack_id != review_id:
        raise ValueError("--review-pack-id does not match review.json")

    if pack_path is not None:
        manifest = load_verified_review_pack(pack_path)
        errors = validate_review_document(review, manifest)
        if errors:
            raise ValueError("review validation failed before composition:\n- " + "\n- ".join(errors))
        if review_id != manifest.get("review_pack_id"):
            raise ValueError("review.json does not belong to --review-pack")
    return review_id, sha256_file(review_path)


async def _repair_binding(
    args: argparse.Namespace,
    *,
    source_path: Path,
    review_pack_id: str,
    review_sha256: str,
) -> dict[str, Any] | None:
    if not args.repair_provenance:
        return None
    sidecar_path = Path(args.repair_provenance)
    provenance = await verify_repair_provenance(
        sidecar_path,
        expected_output_path=source_path,
    )
    if review_pack_id and provenance.get("review_pack_id") != review_pack_id:
        raise ValueError("repair provenance does not belong to the supplied review pack")
    if review_sha256 and provenance.get("review_sha256") != review_sha256:
        raise ValueError("repair provenance does not belong to the supplied review.json")
    return {
        "local_path": str(sidecar_path.resolve(strict=False)),
        "sha256": await asyncio.to_thread(sha256_file, sidecar_path),
        "repair_result_id": provenance.get("repair_result_id"),
    }


async def _verify_embedded_repair_binding(document: dict[str, Any]) -> None:
    source = document.get("source") or {}
    binding = source.get("repair_provenance")
    if binding is None:
        return
    if not isinstance(binding, dict):
        raise ValueError("source.repair_provenance must be an object")
    path = Path(str(binding.get("local_path") or ""))
    expected_output = Path(str(source.get("local_path") or ""))
    provenance = await verify_repair_provenance(path, expected_output_path=expected_output)
    sidecar_sha = await asyncio.to_thread(sha256_file, path)
    if binding.get("sha256") != sidecar_sha:
        raise ValueError("embedded repair provenance SHA-256 changed")
    if binding.get("repair_result_id") != provenance.get("repair_result_id"):
        raise ValueError("embedded repair provenance result ID changed")
    review_pack_id = str(source.get("review_pack_id") or "")
    review_sha256 = str(source.get("review_sha256") or "")
    if review_pack_id and provenance.get("review_pack_id") != review_pack_id:
        raise ValueError("embedded repair provenance review_pack_id mismatch")
    if review_sha256 and provenance.get("review_sha256") != review_sha256:
        raise ValueError("embedded repair provenance review SHA mismatch")


async def _cmd_init(args: argparse.Namespace) -> int:
    source_path = Path(args.source_video)
    probe = await probe_media_async(source_path)
    if not media_probe_is_deliverable(probe):
        raise RuntimeError("composition source must pass a video+audio media probe")
    assert probe is not None
    probed_duration = float(probe.duration)
    if args.duration is not None and abs(float(args.duration) - probed_duration) > 0.75:
        raise ValueError(
            f"declared --duration does not match media probe: "
            f"declared={float(args.duration):.3f}s probe={probed_duration:.3f}s"
        )
    review_pack_id, review_sha256 = _review_binding(args)
    repair_binding = await _repair_binding(
        args,
        source_path=source_path,
        review_pack_id=review_pack_id,
        review_sha256=review_sha256,
    )
    document = build_composition_template(
        source_video_path=source_path,
        source_duration=probed_duration,
        title=args.title,
        performer=args.performer,
        source_review_pack_id=review_pack_id,
        source_review_sha256=review_sha256,
        project_key=args.project_key,
        youtube_account_alias=args.youtube_account_alias,
        youtube_channel_id=args.youtube_channel_id,
    )
    if repair_binding is not None:
        document["source"]["repair_provenance"] = repair_binding
        document = refresh_composition_id(document)
    _write_atomic(Path(args.output), document, overwrite=False)
    print(Path(args.output))
    return 0


def _cmd_refresh(args: argparse.Namespace) -> int:
    document = refresh_composition_id(_load(Path(args.plan)))
    output = Path(args.output or args.plan)
    overwrite = args.output is None or output.resolve(strict=False) == Path(args.plan).resolve(strict=False)
    _write_atomic(output, document, overwrite=overwrite)
    print(output)
    return 0


async def _cmd_validate(args: argparse.Namespace) -> int:
    document = _load(Path(args.plan))
    errors = validate_composition_document(document)
    if errors:
        raise ValueError("composition validation failed:\n- " + "\n- ".join(errors))
    await _verify_embedded_repair_binding(document)
    print(
        json.dumps(
            {
                "valid": True,
                "composition_id": document.get("composition_id"),
                "pieces": len(document.get("pieces") or []),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


async def _cmd_render(args: argparse.Namespace) -> int:
    document = _load(Path(args.plan))
    await _verify_embedded_repair_binding(document)
    results = await render_composition(document, output_dir=Path(args.output_dir))
    handoff = build_release_handoff(document, results)
    handoff_path = Path(args.output_dir) / "editorial-release-handoff.json"
    _write_handoff(handoff_path, handoff)
    print(json.dumps({"results": results, "handoff": str(handoff_path)}, ensure_ascii=False, indent=2))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and render provenance-bound full/excerpt/Short compositions"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="bind an empty composition plan to exact cleaned media")
    init.add_argument("--source-video", required=True)
    init.add_argument(
        "--duration",
        type=float,
        help="optional expected duration; actual plan duration is always taken from media probe",
    )
    init.add_argument("--title", default="")
    init.add_argument("--performer", default="")
    init.add_argument("--review", help="validated review.json used to approve the cleaned source")
    init.add_argument("--review-pack", help="exact review ZIP; with --review this is fully revalidated")
    init.add_argument(
        "--review-pack-id",
        default="",
        help="compatibility/manual ID when the exact review ZIP is not supplied",
    )
    init.add_argument(
        "--repair-provenance",
        help="exact .editorial-repair.json sidecar for a surgically repaired clean source",
    )
    init.add_argument("--project-key", default="")
    init.add_argument("--youtube-account-alias", default="")
    init.add_argument("--youtube-channel-id", default="")
    init.add_argument("--output", required=True)

    refresh = sub.add_parser("refresh-id", help="recompute composition_id after editorial edits")
    refresh.add_argument("--plan", required=True)
    refresh.add_argument("--output")

    validate = sub.add_parser("validate", help="validate an exact composition plan")
    validate.add_argument("--plan", required=True)

    render = sub.add_parser("render", help="render all pieces and provider-inert release handoff")
    render.add_argument("--plan", required=True)
    render.add_argument("--output-dir", required=True)
    return parser


async def _amain(args: argparse.Namespace) -> int:
    if args.command == "init":
        return await _cmd_init(args)
    if args.command == "refresh-id":
        return _cmd_refresh(args)
    if args.command == "validate":
        return await _cmd_validate(args)
    if args.command == "render":
        return await _cmd_render(args)
    raise RuntimeError(f"unknown command: {args.command}")


def main() -> int:
    args = _parser().parse_args()
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
