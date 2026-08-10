#!/usr/bin/env python3
"""PowerShell-friendly CLI for reviewed translation composition."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


def _write(path: Path, data: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _cmd_init(args: argparse.Namespace) -> int:
    document = build_composition_template(
        source_video_path=Path(args.source_video),
        source_duration=args.duration,
        title=args.title,
        performer=args.performer,
        source_review_pack_id=args.review_pack_id,
        source_review_sha256=(sha256_file(Path(args.review)) if args.review else ""),
        project_key=args.project_key,
        youtube_account_alias=args.youtube_account_alias,
        youtube_channel_id=args.youtube_channel_id,
    )
    _write(Path(args.output), document)
    print(Path(args.output))
    return 0


def _cmd_refresh(args: argparse.Namespace) -> int:
    document = refresh_composition_id(_load(Path(args.plan)))
    output = Path(args.output or args.plan)
    _write(output, document)
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
    _write(handoff_path, handoff)
    print(json.dumps({"results": results, "handoff": str(handoff_path)}, ensure_ascii=False, indent=2))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and render provenance-bound full/excerpt/Short compositions"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="bind an empty composition plan to exact cleaned media")
    init.add_argument("--source-video", required=True)
    init.add_argument("--duration", type=float, required=True)
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
        return _cmd_init(args)
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
