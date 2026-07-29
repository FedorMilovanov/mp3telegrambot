#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility facade for the universal Dub Studio wizard.

The existing handler remains in ``handlers/dub_wizard.py``. This package keeps
all handlers and state while routing URL parsing through the same fail-closed
single-video parser used by production downloads.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from tools.voxcpm2 import clean_source_download

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "dub_wizard.py"
_SPEC = importlib.util.spec_from_file_location(
    "handlers._dub_wizard_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить базовый Dub wizard: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))


def _extract_youtube_video_id(value: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    video_id = clean_source_download._url_video_id(raw)
    if not video_id:
        raise ValueError(
            "Нужна каноническая ссылка на один YouTube-ролик: "
            "watch, youtu.be, Shorts, live или embed."
        )
    return video_id, f"https://youtube.com/watch?v={video_id}"


# Legacy callbacks resolve this global when the user sends a URL.
_legacy._extract_youtube_video_id = _extract_youtube_video_id

__all__ = sorted(
    set(name for name in dir(_legacy) if not name.startswith("__"))
    | {"_extract_youtube_video_id"}
)
