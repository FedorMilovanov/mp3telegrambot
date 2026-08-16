#!/usr/bin/env python3
"""Source-own Factory timing evidence and Dub stale-card fallback."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMING = ROOT / "services" / "shorts_factory_timing.py"
FACTORY = ROOT / "pipelines" / "shorts_factory.py"
DUB_COMMANDS = ROOT / "handlers" / "dub_commands.py"
DUB_MULTI = ROOT / "handlers" / "dub_multicommand.py"


def _remove_top_function(text: str, name: str, path: Path) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            start = node.lineno - 1
            end = node.end_lineno or node.lineno
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            del lines[start:end]
            return "".join(lines)
    raise RuntimeError(f"{path}: function {name} not found")


def _replace_top_function(text: str, name: str, source: str, path: Path) -> str:
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            lines[node.lineno - 1 : (node.end_lineno or node.lineno)] = [source.rstrip() + "\n"]
            return "".join(lines)
    raise RuntimeError(f"{path}: function {name} not found")


def factory_timing() -> None:
    text = TIMING.read_text(encoding="utf-8")
    text = text.replace(
        "Evidence is request-local through ``ContextVar``. There is no process-global\n"
        "timeline handoff and no fallback to unverified original-language timestamps.\n",
        "Evidence is passed explicitly by the Factory composition owner. There is no\n"
        "ambient timeline state and no fallback to unverified original-language timestamps.\n",
        1,
    )
    text = text.replace("from contextlib import contextmanager\n", "")
    text = text.replace("from contextvars import ContextVar\n", "")
    text = text.replace("from typing import Any, Iterator, Literal\n", "from typing import Any, Literal\n")
    block = '''_CURRENT_TIMELINE: ContextVar[dict[str, Any] | None] = ContextVar(\n    "factory_ru_boundary_timeline",\n    default=None,\n)\n\n'''
    if block not in text:
        raise RuntimeError("Factory ContextVar declaration not found")
    text = text.replace(block, "", 1)
    text = _remove_top_function(text, "factory_ru_boundary_context", TIMING)
    replacement = '''def align_factory_livedub_candidates(\n    candidates: list[dict[str, Any]],\n    *,\n    source_duration: int | float,\n    evidence: dict[str, Any],\n    candidate_kind: CandidateKind = "short",\n) -> list[dict[str, Any]]:\n    if not candidates:\n        return []\n    timeline = dict(evidence or {})\n    if not timeline:\n        raise RuntimeError(\n            "Exact VOT RU boundary proof is unavailable; "\n            "refusing unverified original-timeline cuts"\n        )\n    return align_candidates_to_ru_speech(\n        candidates,\n        source_duration=source_duration,\n        speech_intervals=list(timeline.get("intervals") or []),\n        delay_seconds=float(timeline.get("delay_seconds") or 0.0),\n        source_speech_intervals=list(timeline.get("source_speech_intervals") or []),\n        source_speech_proof=str(timeline.get("source_speech_proof") or "unavailable"),\n        proof=str(timeline.get("proof") or RU_ONLY_BOUNDARY_PROOF),\n        candidate_kind=candidate_kind,\n    )\n'''
    text = _replace_top_function(text, "align_factory_livedub_candidates", replacement, TIMING)
    text = text.replace(', "factory_ru_boundary_context"', "")
    text = text.replace('"align_factory_livedub_candidates", "factory_ru_boundary_context",\n', '"align_factory_livedub_candidates",\n')
    if "ContextVar" in text or "_CURRENT_TIMELINE" in text or "factory_ru_boundary_context" in text:
        raise RuntimeError("Factory ambient timing state survived")
    ast.parse(text, filename=str(TIMING))
    TIMING.write_text(text, encoding="utf-8")

    pipeline = FACTORY.read_text(encoding="utf-8")
    old_import = '''            from services.shorts_factory_timing import (\n                factory_ru_boundary_context,\n                prepare_factory_ru_boundary_evidence,\n            )\n'''
    new_import = '''            from services.shorts_factory_timing import prepare_factory_ru_boundary_evidence\n'''
    if old_import not in pipeline:
        raise RuntimeError("Factory timing context import block not found")
    pipeline = pipeline.replace(old_import, new_import, 1)
    old_calls = '''            with factory_ru_boundary_context(ru_boundary_evidence):\n                render_shorts = _shift_candidates_for_livedub(\n                    shorts_candidates,\n                    source_duration=render_source_duration,\n                    candidate_kind="short",\n                )\n                render_longs = _shift_candidates_for_livedub(\n                    long_candidates,\n                    source_duration=render_source_duration,\n                    candidate_kind="long",\n                )\n'''
    new_calls = '''            render_shorts = _shift_candidates_for_livedub(\n                shorts_candidates,\n                source_duration=render_source_duration,\n                evidence=ru_boundary_evidence,\n                candidate_kind="short",\n            )\n            render_longs = _shift_candidates_for_livedub(\n                long_candidates,\n                source_duration=render_source_duration,\n                evidence=ru_boundary_evidence,\n                candidate_kind="long",\n            )\n'''
    if old_calls not in pipeline:
        raise RuntimeError("Factory timing context call block not found")
    pipeline = pipeline.replace(old_calls, new_calls, 1)
    if "factory_ru_boundary_context" in pipeline:
        raise RuntimeError("Factory pipeline still references timing context")
    ast.parse(pipeline, filename=str(FACTORY))
    FACTORY.write_text(pipeline, encoding="utf-8")
    print("Factory RU boundary evidence is explicit")


def dub_stale_card() -> None:
    commands = DUB_COMMANDS.read_text(encoding="utf-8")
    anchor = '_NOT_MODIFIED = "message is not modified"\n'
    constants = '''_PERMANENT_EDIT_ERRORS = (\n    "message to edit not found",\n    "message can't be edited",\n    "message cannot be edited",\n    "message identifier is not specified",\n    "message_id_invalid",\n)\n'''
    if "_PERMANENT_EDIT_ERRORS" not in commands:
        if anchor not in commands:
            raise RuntimeError("dub_commands _NOT_MODIFIED anchor missing")
        commands = commands.replace(anchor, anchor + constants, 1)
    replacement = '''async def _safe_edit(query: Any, text: str, **kwargs: Any) -> bool:\n    """Edit callback card; replace permanently stale cards with a new message."""\n    try:\n        await query.edit_message_text(text, **kwargs)\n        return True\n    except BadRequest as exc:\n        detail = str(exc or "").casefold()\n        if _NOT_MODIFIED in detail:\n            return False\n        if not any(marker in detail for marker in _PERMANENT_EDIT_ERRORS):\n            raise\n        message = getattr(query, "message", None)\n        if message is None or not hasattr(message, "reply_text"):\n            raise\n        await message.reply_text(text, **kwargs)\n        return True\n'''
    commands = _replace_top_function(commands, "_safe_edit", replacement, DUB_COMMANDS)
    ast.parse(commands, filename=str(DUB_COMMANDS))
    DUB_COMMANDS.write_text(commands, encoding="utf-8")

    multi = DUB_MULTI.read_text(encoding="utf-8")
    multi = multi.replace('"""Reliable multiline Dub commands and stale callback-card recovery."""', '"""Reliable multiline Dub command dispatch."""', 1)
    multi = multi.replace("from telegram.error import BadRequest\n", "")
    stale_start = multi.find("_PERMANENT_EDIT_ERRORS = (")
    if stale_start >= 0:
        stale_end = multi.find(")\n", stale_start)
        if stale_end < 0:
            raise RuntimeError("dub_multicommand stale constants malformed")
        multi = multi[:stale_start] + multi[stale_end + 2 :]
    if "def _permanent_edit_failure" in multi:
        multi = _remove_top_function(multi, "_permanent_edit_failure", DUB_MULTI)
    if "def install_stale_card_fallback" in multi:
        multi = _remove_top_function(multi, "install_stale_card_fallback", DUB_MULTI)
    multi = multi.replace("    install_stale_card_fallback()\n", "")
    multi = multi.replace('    "install_stale_card_fallback",\n', "")
    if "dub_commands._safe_edit =" in multi or "install_stale_card_fallback" in multi or "_dub_stale_card_fallback" in multi:
        raise RuntimeError("Dub stale-card runtime rebind survived")
    ast.parse(multi, filename=str(DUB_MULTI))
    DUB_MULTI.write_text(multi, encoding="utf-8")
    print("Dub stale-card fallback is source-owned")


def main() -> int:
    factory_timing()
    dub_stale_card()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
