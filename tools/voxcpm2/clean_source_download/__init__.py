#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-network compatibility facade for verified YouTube source downloads.

The durable cache/downloader remains in ``clean_source_download.py``. This
package preserves its API and checks URL identity against the project request
before the first yt-dlp metadata request.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "clean_source_download.py"
_SPEC = importlib.util.spec_from_file_location(
    "tools.voxcpm2._clean_source_download_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить clean source downloader: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_legacy_download_source = _legacy.download_source


def download_source(url: str, source: Path) -> dict[str, Any]:
    source = Path(source)
    url_video_id = _legacy._url_video_id(str(url))
    if not url_video_id:
        raise RuntimeError(
            "Источник должен быть канонической ссылкой на один YouTube-ролик."
        )
    project_video_id = _legacy._project_request_video_id(source)
    if project_video_id and project_video_id != url_video_id:
        raise RuntimeError(
            "Project request и YouTube URL имеют разные video ID до yt-dlp: "
            f"request={project_video_id}, url={url_video_id}."
        )
    return _legacy_download_source(str(url), source)


_legacy.download_source = download_source

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {"download_source"}
)
