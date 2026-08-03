#!/usr/bin/env python3
"""Move synchronous encoder capability selection off asyncio event loops."""
from __future__ import annotations

import ast
from pathlib import Path


TARGETS = {
    Path("services/shorts_video.py"): {
        "_unowned_render_short_clip",
        "_unowned_short_transform",
        "_unowned_burn_subtitles_into_short",
    },
    Path("services/render_clips_montage.py"): {
        "render_clip",
        "render_montage_short",
    },
    Path("services/eng_subtitles.py"): {"_burn_subtitles"},
    Path("services/shorts_subtitle_burn.py"): {"burn_subtitles_into_short"},
}


def _offsets(source: str) -> list[int]:
    values = [0]
    for line in source.splitlines(keepends=True):
        values.append(values[-1] + len(line))
    return values


def _add_owner_import(path: Path, source: str) -> str:
    if "from services.async_worker import await_owned_coroutine\n" in source:
        return source

    if path.name == "render_clips_montage.py":
        anchor = "from services.async_process import run_cancellable_process\n"
    elif path.name == "shorts_subtitle_burn.py":
        anchor = "from services.async_process import run_cancellable_process\n"
    else:
        raise SystemExit(f"{path}: missing owner import unexpectedly")

    if source.count(anchor) != 1:
        raise SystemExit(f"{path}: owner import anchor count changed")
    return source.replace(
        anchor,
        anchor + "from services.async_worker import await_owned_coroutine\n",
        1,
    )


def _patch_file(path: Path, function_names: set[str]) -> None:
    source = _add_owner_import(path, path.read_text(encoding="utf-8"))
    tree = ast.parse(source)
    offsets = _offsets(source)
    replacements: list[tuple[int, int, str]] = []
    seen: dict[str, int] = {}

    for function in tree.body:
        if not isinstance(function, ast.AsyncFunctionDef):
            continue
        if function.name not in function_names:
            continue

        matches: list[ast.Assign] = []
        for node in ast.walk(function):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            call = node.value
            if not isinstance(call, ast.Call):
                continue
            if not isinstance(call.func, ast.Name) or call.func.id != "_get_video_encoder":
                continue
            if call.args or call.keywords:
                raise SystemExit(f"{path}:{function.name}: encoder call gained arguments")
            matches.append(node)

        if len(matches) != 1:
            raise SystemExit(
                f"{path}:{function.name}: expected one encoder assignment, "
                f"found {len(matches)}"
            )
        assignment = matches[0]
        target = ast.get_source_segment(source, assignment.targets[0])
        if not target:
            raise SystemExit(f"{path}:{function.name}: assignment target unavailable")
        indent = " " * assignment.col_offset
        replacement = (
            f"{target} = await await_owned_coroutine(\n"
            f"{indent}    asyncio.to_thread(_get_video_encoder)\n"
            f"{indent})"
        )
        start = offsets[assignment.lineno - 1] + assignment.col_offset
        end = offsets[assignment.end_lineno - 1] + assignment.end_col_offset
        replacements.append((start, end, replacement))
        seen[function.name] = 1

    if set(seen) != function_names:
        missing = sorted(function_names - set(seen))
        raise SystemExit(f"{path}: encoder functions missing: {missing}")

    for start, end, replacement in sorted(replacements, reverse=True):
        source = source[:start] + replacement + source[end:]

    if path.name == "shorts_video.py":
        ugly = (
            "            segments, audio_duration, detected_lang, lang_prob = await await_owned_coroutine(\n"
            "            asyncio.to_thread(_run_whisper)\n"
            "        )"
        )
        clean = (
            "            segments, audio_duration, detected_lang, lang_prob = await await_owned_coroutine(\n"
            "                asyncio.to_thread(_run_whisper)\n"
            "            )"
        )
        if source.count(ugly) != 1:
            raise SystemExit("shorts_video.py: Whisper indentation marker changed")
        source = source.replace(ugly, clean, 1)

    ast.parse(source)
    selected = []
    parsed = ast.parse(source)
    for function in parsed.body:
        if isinstance(function, ast.AsyncFunctionDef) and function.name in function_names:
            segment = ast.get_source_segment(source, function) or ""
            selected.append(segment)
    joined = "\n".join(selected)
    if joined.count("await await_owned_coroutine(") != len(function_names):
        raise SystemExit(f"{path}: owned encoder wrapper count changed")
    if joined.count("asyncio.to_thread(_get_video_encoder)") != len(function_names):
        raise SystemExit(f"{path}: encoder to_thread count changed")
    if "= _get_video_encoder()" in joined:
        raise SystemExit(f"{path}: direct encoder call remains")

    path.write_text(source, encoding="utf-8")


def main() -> None:
    for path, functions in TARGETS.items():
        _patch_file(path, functions)

    total = sum(len(functions) for functions in TARGETS.values())
    if total != 7:
        raise SystemExit(f"expected seven call sites, found {total}")
    print("moved seven encoder capability selections off event loops")


if __name__ == "__main__":
    main()
