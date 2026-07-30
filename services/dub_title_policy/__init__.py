#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility facade preserving Dub title-policy monkeypatch semantics.

The canonical title implementation remains in ``services/dub_title_policy.py``.
Package facades keep legacy functions for stability, but those functions resolve
globals in their original modules. After the normal title-policy patch runs,
this facade mirrors the health wrapper into the legacy health module so
``/dubcheck`` executes it rather than only exposing it as a package attribute.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_LEGACY_PATH = Path(__file__).resolve().parents[1] / "dub_title_policy.py"
_SPEC = importlib.util.spec_from_file_location(
    "services._dub_title_policy_legacy",
    _LEGACY_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Не удалось загрузить Dub title policy: {_LEGACY_PATH}")
_legacy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))

_legacy_patch_health = _legacy._patch_health


def _patch_health() -> None:
    _legacy_patch_health()
    try:
        import handlers.dub_health as health
    except Exception:
        return
    legacy_health = getattr(health, "_legacy", None)
    wrapped = getattr(health, "collect_dub_health", None)
    if legacy_health is not None and callable(wrapped):
        legacy_health.collect_dub_health = wrapped


# install_dub_title_policy resolves this global in the legacy module at call time.
_legacy._patch_health = _patch_health

install_dub_title_policy = _legacy.install_dub_title_policy
install_voxcpm_title_policy = _legacy.install_voxcpm_title_policy
canonical_delivery_filename = _legacy.canonical_delivery_filename
canonical_media_title = _legacy.canonical_media_title
RU_SERVICE_WORDS = _legacy.RU_SERVICE_WORDS

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | {"_patch_health"}
)
