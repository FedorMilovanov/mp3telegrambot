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
from services.translation_editorial import sha256_file
from services.translation_editorial_composition import (
    build_composition_template,
    build_release_handoff,
    refresh_composition_id,
    render_composition,
    validate_composition_document,
)


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
                with path.open("x", encoding="utf-8") as stream:
                    stream.write(payload)
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
    document = build_composition_template(
        source_video_path=source_path,
        source_duration=probed_duration,
        title=args.title,
        performer=args.performer,
        source_review_pack_id=args.review_pack_id,
        source_review_sha256=(sha256_file(Path(args.review)) if args.review else ""),
        project_key=args.project_key,
        youtube_account_alias=args.youtube_account_alias,
        youtube_channel_id=args.youtube_channel_id,
    )
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


def _cmd_validate(args: argparse.Namespace) -> int:
    document = _load(Path(args.plan))
    errors = validate_composition_document(document)
    if errors:
        raise ValueError("composition validation failed:\n- " + "\n- ".join(errors))
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
    init.add_argument("--review")
    init.add_argument("--review-pack-id", default="")
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
        return _cmd_validate(args)
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
