#!/usr/bin/env python3
"""Remove now-unused nested event-loop aliases after process-owner migration."""
from __future__ import annotations

import ast
from pathlib import Path


TARGETS = {
    Path("services/shorts_video.py"): {
        "_unowned_download_video_for_shorts",
        "_unowned_transcribe_short_clip",
        "_unowned_burn_subtitles_into_short",
        "_unowned_create_short_title_poster",
    },
    Path("services/render_clips_montage.py"): {"render_montage_short"},
}


def _offsets(source: str) -> list[int]:
    values = [0]
    for line in source.splitlines(keepends=True):
        values.append(values[-1] + len(line))
    return values


def main() -> None:
    removed_total = 0
    for path, names in TARGETS.items():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        offsets = _offsets(source)
        removals: list[tuple[int, int]] = []

        for function in tree.body:
            if not isinstance(function, ast.AsyncFunctionDef) or function.name not in names:
                continue
            loop_loads = [
                node
                for node in ast.walk(function)
                if isinstance(node, ast.Name)
                and node.id == "loop"
                and isinstance(node.ctx, ast.Load)
            ]
            if loop_loads:
                raise SystemExit(f"{path}:{function.name}: loop is still read")

            assignments = []
            for node in ast.walk(function):
                if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                    continue
                target = node.targets[0]
                value = node.value
                if not isinstance(target, ast.Name) or target.id != "loop":
                    continue
                if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Attribute):
                    continue
                if not (
                    isinstance(value.func.value, ast.Name)
                    and value.func.value.id == "asyncio"
                    and value.func.attr == "get_running_loop"
                ):
                    continue
                assignments.append(node)

            if len(assignments) != 1:
                raise SystemExit(
                    f"{path}:{function.name}: expected one unused loop assignment, "
                    f"found {len(assignments)}"
                )
            node = assignments[0]
            start = offsets[node.lineno - 1]
            end = offsets[node.end_lineno]
            removals.append((start, end))

        if len(removals) != len(names):
            raise SystemExit(
                f"{path}: expected {len(names)} removals, found {len(removals)}"
            )
        for start, end in sorted(removals, reverse=True):
            source = source[:start] + source[end:]
        ast.parse(source)
        path.write_text(source, encoding="utf-8")
        removed_total += len(removals)

    if removed_total != 5:
        raise SystemExit(f"expected five loop removals, found {removed_total}")
    print("removed five unused nested event-loop aliases")


if __name__ == "__main__":
    main()
