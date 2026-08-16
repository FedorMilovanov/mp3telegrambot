#!/usr/bin/env python3
"""Temporary cleanup: remove unreachable installer shells and static runtime facade."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMP_TOOLS = {
    "tools/pure_policy_cleanup.py",
    "tools/runtime_reference_audit.py",
    "tools/runtime_surgery_audit.py",
    "tools/installer_call_audit.py",
    "tools/dead_runtime_cleanup.py",
    "tools/zero_runtime_marathon.py",
    "tools/repair_title_runner.py",
}


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, text: str) -> None:
    (ROOT / rel).write_text(text, encoding="utf-8")


def main() -> int:
    old = ROOT / "services/shorts_static_runtime.py"
    new = ROOT / "services/shorts_static_policy.py"
    if not old.is_file() or new.exists():
        raise RuntimeError("static policy move precondition failed")
    text = old.read_text(encoding="utf-8")
    text, count = re.subn(
        r"\n\ndef install_short_static_runtime\(\) -> str:\n.*?\n    return \"source-owned static-video classifier; no runtime rebinding\"\n?",
        "",
        text,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError("static installer block not found")
    new.write_text(text.rstrip() + "\n", encoding="utf-8")
    old.unlink()
    ffmpeg = read("services/ffmpeg.py")
    needle = "from services.shorts_static_runtime import _is_static_video_confident"
    if needle not in ffmpeg:
        raise RuntimeError("ffmpeg static import anchor missing")
    write("services/ffmpeg.py", ffmpeg.replace(needle, "from services.shorts_static_policy import _is_static_video_confident", 1))

    rel = "services/shorts_factory_publication.py"
    text = read(rel).replace("_INSTALLED = False\n", "")
    text, count = re.subn(
        r"\n\ndef install_factory_publication_formatters\(shorts_module, clips_module\) -> bool:\n.*?\n    return True\n",
        "",
        text,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError("Factory publication installer block not found")
    text = text.replace('    "install_factory_publication_formatters",\n', "")
    write(rel, text)

    rel = "services/shorts_factory_video_quality.py"
    text = read(rel)
    text = text.replace("from contextvars import ContextVar\n", "")
    text = re.sub(
        r"_SOURCE_METADATA: ContextVar\[dict\[str, str\] \| None\] = ContextVar\(\n    \"factory_publication_source_metadata\",\n    default=None,\n\)\n_INSTALLED = False\n",
        "",
        text,
        count=1,
    )
    text, count_active = re.subn(
        r"\n\ndef _factory_active\(\) -> bool:\n.*?\n        return False\n",
        "",
        text,
        count=1,
        flags=re.DOTALL,
    )
    if count_active != 1:
        raise RuntimeError("Factory active ambient helper not found")
    text, count_meta = re.subn(
        r"\n\ndef _source_context\(url: str, info: dict\[str, Any\]\) -> dict\[str, str\]:\n.*?(?=\n\ndef install_factory_video_quality_policy\(\) -> bool:)",
        "",
        text,
        count=1,
        flags=re.DOTALL,
    )
    if count_meta != 1:
        raise RuntimeError("Factory metadata/installer prelude not found")
    text, count_install = re.subn(
        r"\n\ndef install_factory_video_quality_policy\(\) -> bool:\n.*?\n    return True\n",
        "",
        text,
        count=1,
        flags=re.DOTALL,
    )
    if count_install != 1:
        raise RuntimeError("Factory video-quality installer not found")
    text = text.replace('    "current_factory_source_metadata",\n', "")
    text = text.replace('    "install_factory_video_quality_policy",\n', "")
    write(rel, text)

    blockers = []
    for path in ROOT.rglob("*.py"):
        relp = path.relative_to(ROOT).as_posix()
        if relp.startswith("tests/") or relp in TEMP_TOOLS:
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if "shorts_static_runtime" in source:
            blockers.append(relp)
    if blockers:
        raise RuntimeError("old static runtime references remain: " + ", ".join(blockers))
    print("pure policy cleanup complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
