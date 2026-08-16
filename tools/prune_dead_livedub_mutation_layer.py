#!/usr/bin/env python3
"""Remove LiveDub mutation wrappers proven to have zero production root calls."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEDUPE = ROOT / "services" / "livedub_audio_dedupe.py"
OUTPUT = ROOT / "services" / "livedub_output_policy.py"
PUBLICATION = ROOT / "services" / "livedub_publication.py"


def remove_functions(path: Path, names: set[str]) -> None:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    spans: list[tuple[int, int]] = []
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            found.add(node.name)
            spans.append((node.lineno - 1, node.end_lineno or node.lineno))
    missing = names - found
    if missing:
        raise RuntimeError(f"{path}: missing dead functions {sorted(missing)}")
    lines = text.splitlines(keepends=True)
    for start, end in sorted(spans, reverse=True):
        while start > 0 and not lines[start - 1].strip():
            start -= 1
        del lines[start:end]
    path.write_text("".join(lines), encoding="utf-8")


def clean_output_policy() -> None:
    remove_functions(
        OUTPUT,
        {
            "_patch_pipeline_title",
            "_wrap_send_video",
            "_wrap_send_audio",
            "harden_livedub_audio_dedupe",
        },
    )
    text = OUTPUT.read_text(encoding="utf-8")
    text = text.replace(
        "This runtime adapter adds a final, cheap title\ntranslation pass and keeps provider/implementation labels out of user-facing\ncaptions.\n\nIt is intentionally installed *after* ``main`` is imported and *before* the\nLiveDub audio companion.  The companion can still recognise the original\ninternal caption marker, while the actual Telegram API receives the clean\npublication caption produced here.\n",
        "This module owns pure title/author and caption normalization helpers used by\nthe explicit LiveDub publication path. It does not intercept Telegram methods or\nrebind pipeline functions at runtime.\n",
        1,
    )
    text = text.replace("import threading\n", "")
    text = text.replace("from pathlib import Path\n", "")
    text = text.replace("_INSTALL_LOCK = threading.Lock()\n", "")
    forbidden = (
        "setattr(cls",
        "pipelines.main_pipeline as pipeline",
        "pipeline._translate_livedub_title_for_caption =",
        "livedub_audio_dedupe",
        "_mp3bot_output_policy",
    )
    bad = [token for token in forbidden if token in text]
    if bad:
        raise RuntimeError(f"LiveDub output mutation survived: {bad}")
    ast.parse(text, filename=str(OUTPUT))
    OUTPUT.write_text(text, encoding="utf-8")


def clean_publication() -> None:
    remove_functions(
        PUBLICATION,
        {"_wrap_send_video", "_wrap_send_audio", "_reuse_and_suppress_legacy_info_card"},
    )
    text = PUBLICATION.read_text(encoding="utf-8")
    text = text.replace(
        "The internal pipeline needs provider markers so the audio companion can recognise a\n"
        "successful LiveDub send. Users do not need those implementation labels. This\n"
        "adapter is installed between ``livedub_output_policy`` and the audio companion:\n\n"
        "* the outer companion still sees the private marker;\n"
        "* the Telegram API receives a Russian title and description generated on the\n"
        "  source-owned Gemini 3.6/HIGH semantic route, plus a link to the source video;\n"
        "* the MP3 caption contains the useful description/link only — never labels such\n"
        "  as ``Русская аудиоверсия`` or ``Живые голоса Яндекса``;\n"
        "* the old separate ENG Quick info card is satisfied from the same cache and\n"
        "  suppressed, so there is one polished publication block rather than duplicates.\n",
        "The explicit LiveDub delivery path calls these source-owned helpers before sending\n"
        "video and MP3 results. No Telegram methods, info-card functions or pipeline modules\n"
        "are intercepted or rebound at runtime.\n",
        1,
    )
    text = text.replace("import contextvars\n", "")
    text = text.replace("import threading\n", "")
    text = text.replace("_INSTALL_LOCK = threading.Lock()\n", "")
    context_block = '''_CURRENT_SOURCE_URL: contextvars.ContextVar[str] = contextvars.ContextVar(\n    "mp3bot_livedub_source_url", default=""\n)\n'''
    if context_block not in text:
        raise RuntimeError("LiveDub publication ContextVar declaration not found")
    text = text.replace(context_block, "", 1)
    text = text.replace(
        "    candidate = _plain(value or _CURRENT_SOURCE_URL.get(), 600)\n",
        "    candidate = _plain(value, 600)\n",
        1,
    )
    # The old output-policy translation fallback was an installer-era seam and no
    # longer has a source-owned implementation. The primary publication Gemini route
    # already owns translation; deterministic fallback keeps truthful metadata only.
    old_fallback = '''        try:\n            from services.livedub_output_policy import _translate_title_line\n\n            translated = await _translate_title_line(source_line)\n            if translated:\n                title, translated_author = translated\n                author = translated_author or author\n        except Exception as exc:\n            logger.info("[LiveDubPublication] title fallback failed: %s", str(exc)[:140])\n'''
    if old_fallback not in text:
        raise RuntimeError("legacy publication title fallback seam not found")
    text = text.replace(old_fallback, "", 1)
    forbidden = (
        "ContextVar",
        "_CURRENT_SOURCE_URL",
        "setattr(cls",
        "module.build_livedub_info_card =",
        "module.format_livedub_info_message =",
        "_mp3bot_publication_card",
        "inline_publication",
    )
    bad = [token for token in forbidden if token in text]
    if bad:
        raise RuntimeError(f"LiveDub publication mutation survived: {bad}")
    ast.parse(text, filename=str(PUBLICATION))
    PUBLICATION.write_text(text, encoding="utf-8")


def main() -> int:
    # Explicit SourceAudioDeferral in livedub_delivery_coordinator supersedes this
    # old interception-only module. Prove no production import remains in workflow.
    DEDUPE.unlink()
    clean_output_policy()
    clean_publication()
    print("removed dead LiveDub interception layer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
