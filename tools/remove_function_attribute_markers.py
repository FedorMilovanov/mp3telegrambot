#!/usr/bin/env python3
"""Replace mutable function-attribute markers with module-owned state."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVEDUB = ROOT / "services" / "livedub_info.py"
FACTORY = ROOT / "services" / "shorts_factory_publication.py"


def main() -> int:
    livedub = LIVEDUB.read_text(encoding="utf-8")
    marker = '''\n\n# Native request-local multi-client support is concurrency-safe.\nbuild_livedub_info_card._mp3bot_all_clients = True  # type: ignore[attr-defined]\n'''
    if marker not in livedub:
        raise RuntimeError("LiveDub function-attribute marker not found")
    livedub = livedub.replace(marker, "", 1)
    if "_mp3bot_all_clients" in livedub:
        raise RuntimeError("LiveDub mutable function marker survived")
    ast.parse(livedub, filename=str(LIVEDUB))
    LIVEDUB.write_text(livedub, encoding="utf-8")

    factory = FACTORY.read_text(encoding="utf-8")
    factory = factory.replace("import os\n", "import os\nimport weakref\n", 1)
    state_anchor = '_DESCRIPTION_FIELD = "_factory_publication_description"\n'
    if state_anchor not in factory:
        raise RuntimeError("Factory publication state anchor missing")
    factory = factory.replace(
        state_anchor,
        state_anchor + "_WRAPPED_CAPTION_BUILDERS: weakref.WeakSet[Callable[..., str]] = weakref.WeakSet()\n",
        1,
    )
    old = '''def wrap_factory_caption_builder(builder: Callable[..., str]) -> Callable[..., str]:\n    """Insert only explicitly Factory-enriched prose; otherwise be a true no-op."""\n    if getattr(builder, "_factory_publication_polish", False):\n        return builder\n\n    def wrapped(*args, **kwargs):\n        candidate = _candidate_from_call(args, kwargs)\n        caption = builder(*args, **kwargs)\n        return _insert_description(caption, candidate.get(_DESCRIPTION_FIELD))\n\n    wrapped._factory_publication_polish = True  # type: ignore[attr-defined]\n    return wrapped\n'''
    new = '''def wrap_factory_caption_builder(builder: Callable[..., str]) -> Callable[..., str]:\n    """Insert only explicitly Factory-enriched prose; otherwise be a true no-op."""\n    if builder in _WRAPPED_CAPTION_BUILDERS:\n        return builder\n\n    def wrapped(*args, **kwargs):\n        candidate = _candidate_from_call(args, kwargs)\n        caption = builder(*args, **kwargs)\n        return _insert_description(caption, candidate.get(_DESCRIPTION_FIELD))\n\n    _WRAPPED_CAPTION_BUILDERS.add(wrapped)\n    return wrapped\n'''
    if old not in factory:
        raise RuntimeError("Factory publication wrapper marker block not found")
    factory = factory.replace(old, new, 1)
    if "_factory_publication_polish" in factory:
        raise RuntimeError("Factory mutable function marker survived")
    ast.parse(factory, filename=str(FACTORY))
    FACTORY.write_text(factory, encoding="utf-8")
    print("function-attribute markers replaced by module-owned state")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
