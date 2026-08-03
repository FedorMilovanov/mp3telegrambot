#!/usr/bin/env python3
"""Move active Shorts/Clips render subprocesses to the shared tree owner."""
from __future__ import annotations

import ast
from pathlib import Path


SHORTS = Path("services/shorts_video.py")
CLIPS = Path("services/render_clips_montage.py")

SHORTS_FUNCTIONS = {
    "_unowned_download_video_for_shorts": (1, 0),
    "_unowned_render_short_clip": (1, 0),
    "_unowned_short_transform": (1, 0),
    "_unowned_transcribe_short_clip": (1, 1),
    "_unowned_burn_subtitles_into_short": (1, 0),
    "_unowned_create_short_title_poster": (1, 1),
    "_unowned_create_short_snapshot": (1, 0),
}
CLIP_FUNCTIONS = {
    "render_clip": (1, 0),
    "create_clip_snapshot": (1, 0),
    "render_montage_short": (2, 0),
}


def _offsets(source: str) -> list[int]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _span(node: ast.AST, offsets: list[int]) -> tuple[int, int]:
    assert hasattr(node, "lineno") and hasattr(node, "end_lineno")
    start = offsets[node.lineno - 1] + node.col_offset
    end = offsets[node.end_lineno - 1] + node.end_col_offset
    return start, end


def _call_name(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return tuple(reversed(parts))


def _source_segment(source: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(source, node)
    if segment is None:
        raise SystemExit(f"cannot recover source for {type(node).__name__}")
    return segment


def _executor_replacement(source: str, await_node: ast.Await) -> tuple[str, str]:
    call = await_node.value
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
        raise SystemExit("unexpected await shape")
    if call.func.attr != "run_in_executor" or len(call.args) < 2:
        raise SystemExit("unexpected executor call")

    worker = call.args[1]
    if isinstance(worker, ast.Name):
        if worker.id not in {"_run_whisper", "_draw_poster"}:
            raise SystemExit(f"unexpected native worker: {worker.id}")
        return (
            "thread",
            f"await await_owned_coroutine(\n"
            f"            asyncio.to_thread({worker.id})\n"
            f"        )",
        )

    if not isinstance(worker, ast.Lambda) or not isinstance(worker.body, ast.Call):
        raise SystemExit("executor worker is not a subprocess lambda")
    run_call = worker.body
    if _call_name(run_call.func) != ("subprocess", "run"):
        raise SystemExit("executor lambda is not subprocess.run")
    if not run_call.args:
        raise SystemExit("subprocess.run command missing")

    command_node = run_call.args[0]
    command = _source_segment(source, command_node)
    if isinstance(command_node, ast.Name):
        for arg, default in zip(
            worker.args.args[-len(worker.args.defaults):],
            worker.args.defaults,
        ):
            if arg.arg == command_node.id:
                command = _source_segment(source, default)
                break

    timeout: str | None = None
    text = False
    for keyword in run_call.keywords:
        if keyword.arg == "timeout":
            timeout = _source_segment(source, keyword.value)
        elif keyword.arg == "text":
            text = isinstance(keyword.value, ast.Constant) and keyword.value.value is True
        elif keyword.arg in {"capture_output", "encoding", "errors"}:
            continue
        else:
            raise SystemExit(f"unsupported subprocess keyword: {keyword.arg}")
    if timeout is None:
        raise SystemExit("subprocess timeout missing")

    args = f"{command}, timeout={timeout}"
    if text:
        args += ", text=True"
    return "process", f"await run_cancellable_process({args})"


def _patch_functions(
    source: str,
    expected: dict[str, tuple[int, int]],
) -> str:
    tree = ast.parse(source)
    offsets = _offsets(source)
    replacements: list[tuple[int, int, str]] = []
    seen: dict[str, list[int]] = {}

    for function in tree.body:
        if not isinstance(function, ast.AsyncFunctionDef) or function.name not in expected:
            continue
        counts = [0, 0]
        for node in ast.walk(function):
            if not isinstance(node, ast.Await):
                continue
            call = node.value
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            if call.func.attr != "run_in_executor":
                continue
            kind, replacement = _executor_replacement(source, node)
            counts[0 if kind == "process" else 1] += 1
            start, end = _span(node, offsets)
            replacements.append((start, end, replacement))
        seen[function.name] = counts

    for name, wanted in expected.items():
        actual = tuple(seen.get(name, []))
        if actual != wanted:
            raise SystemExit(f"{name}: expected {wanted}, found {actual}")

    for start, end, replacement in sorted(replacements, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source


def _remove_unused_loop_assignments(source: str, names: set[str]) -> str:
    tree = ast.parse(source)
    offsets = _offsets(source)
    removals: list[tuple[int, int]] = []
    for function in tree.body:
        if not isinstance(function, ast.AsyncFunctionDef) or function.name not in names:
            continue
        loop_loads = [
            node for node in ast.walk(function)
            if isinstance(node, ast.Name) and node.id == "loop" and isinstance(node.ctx, ast.Load)
        ]
        if loop_loads:
            continue
        for statement in function.body:
            if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                continue
            target = statement.targets[0]
            value = statement.value
            if not isinstance(target, ast.Name) or target.id != "loop":
                continue
            if not isinstance(value, ast.Call) or _call_name(value.func) != ("asyncio", "get_running_loop"):
                continue
            start = offsets[statement.lineno - 1]
            end = offsets[statement.end_lineno]
            removals.append((start, end))
    for start, end in sorted(removals, reverse=True):
        source = source[:start] + source[end:]
    return source


def _function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        item for item in tree.body
        if isinstance(item, ast.AsyncFunctionDef) and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


def main() -> None:
    shorts = SHORTS.read_text(encoding="utf-8")
    if "from services.async_process import run_cancellable_process\n" not in shorts:
        anchor = "from services.async_worker import await_owned_coroutine\n"
        if shorts.count(anchor) != 1:
            raise SystemExit("shorts owner import anchor changed")
        shorts = shorts.replace(
            anchor,
            anchor + "from services.async_process import run_cancellable_process\n",
            1,
        )
    shorts = _patch_functions(shorts, SHORTS_FUNCTIONS)
    shorts = _remove_unused_loop_assignments(shorts, set(SHORTS_FUNCTIONS))
    old_comment = (
        "# ─── Cancellation ownership boundary for legacy executor work ────────────────\n"
        "#\n"
        "# The implementations above predate the shared asyncio process owner and still\n"
        "# contain a few bounded ``run_in_executor`` calls. Cancelling their asyncio\n"
        "# Future cannot stop native thread work. Public callers therefore use a shielded\n"
        "# ownership boundary: the inner operation and its semaphore/temp-file cleanup\n"
        "# finish first, then caller cancellation is propagated.\n"
    )
    new_comment = (
        "# ─── Public transaction boundary for render work ─────────────────────────────\n"
        "#\n"
        "# External processes use the shared process-tree owner and native threads use\n"
        "# await_owned_coroutine. Public wrappers retain the outer transaction boundary\n"
        "# so semaphore and temporary-file cleanup completes before cancellation returns.\n"
    )
    if old_comment in shorts:
        shorts = shorts.replace(old_comment, new_comment, 1)

    clips = CLIPS.read_text(encoding="utf-8")
    if "from services.async_process import run_cancellable_process\n" not in clips:
        anchor = "from services.ffmpeg import _is_static_video     # AUDIT R28\n"
        if clips.count(anchor) != 1:
            raise SystemExit("clips owner import anchor changed")
        clips = clips.replace(
            anchor,
            anchor + "from services.async_process import run_cancellable_process\n",
            1,
        )
    clips = _patch_functions(clips, CLIP_FUNCTIONS)
    clips = _remove_unused_loop_assignments(clips, set(CLIP_FUNCTIONS))

    ast.parse(shorts)
    ast.parse(clips)
    shorts_selected = "\n".join(_function_source(shorts, name) for name in SHORTS_FUNCTIONS)
    clips_selected = "\n".join(_function_source(clips, name) for name in CLIP_FUNCTIONS)
    if "run_in_executor" in shorts_selected or "subprocess.run(" in shorts_selected:
        raise SystemExit("legacy executor remains in Shorts render surface")
    if shorts_selected.count("await run_cancellable_process(") != 7:
        raise SystemExit("Shorts process owner count changed")
    if shorts_selected.count("asyncio.to_thread(") != 2:
        raise SystemExit("Shorts native thread owner count changed")
    if "run_in_executor" in clips_selected or "subprocess.run(" in clips_selected:
        raise SystemExit("legacy executor remains in Clips render surface")
    if clips_selected.count("await run_cancellable_process(") != 4:
        raise SystemExit("Clips process owner count changed")

    SHORTS.write_text(shorts, encoding="utf-8")
    CLIPS.write_text(clips, encoding="utf-8")
    print("patched 11 external render processes and 2 native workers")


if __name__ == "__main__":
    main()
