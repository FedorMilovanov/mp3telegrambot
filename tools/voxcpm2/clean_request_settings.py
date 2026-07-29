#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical user-visible settings for clean Dub Studio routes."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

POLICY = "clean-request-settings-v1"
MAX_RUSSIAN_DELAY_MS = 1500


def _setting(request: dict[str, Any], key: str, default: Any) -> Any:
    return default if key not in request or request[key] is None else request[key]


def original_level(request: dict[str, Any]) -> float:
    raw = _setting(request, "original_level", 0.18)
    if isinstance(raw, bool):
        raise RuntimeError("original_level не может быть bool.")
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректный original_level={raw!r}.") from exc
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise RuntimeError("original_level должен быть конечным числом в диапазоне 0..1.")
    return value


def russian_delay_ms(request: dict[str, Any]) -> int:
    raw = _setting(request, "russian_delay_ms", 420)
    if isinstance(raw, bool):
        raise RuntimeError("russian_delay_ms не может быть bool.")
    if isinstance(raw, float) and (
        not math.isfinite(raw) or not raw.is_integer()
    ):
        raise RuntimeError("russian_delay_ms должен быть целым числом.")
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"Некорректный russian_delay_ms={raw!r}.") from exc
    if not 0 <= value <= MAX_RUSSIAN_DELAY_MS:
        raise RuntimeError(
            f"russian_delay_ms должен быть в диапазоне 0..{MAX_RUSSIAN_DELAY_MS}."
        )
    return value


def values(request: dict[str, Any]) -> dict[str, float | int | str]:
    if not isinstance(request, dict):
        raise RuntimeError("Dub request должен быть JSON-объектом.")
    return {
        "policy": POLICY,
        "original_level": original_level(request),
        "russian_delay_ms": russian_delay_ms(request),
    }


def _percent_label(level: float) -> str:
    value = level * 100.0
    return f"{value:.3f}".rstrip("0").rstrip(".").replace(".", ",") + "%"


def _correct_label(label: str, *, level: float, delay_ms: int) -> str:
    text = str(label or "")
    text = re.sub(
        r"оригинал\s+\d+(?:[.,]\d+)?%",
        f"оригинал {_percent_label(level)}",
        text,
        flags=re.IGNORECASE,
    )
    delay_text = "без задержки" if delay_ms == 0 else f"с задержкой {delay_ms} мс"
    text = re.sub(
        r"с\s+задержкой\s+\d+\s*мс",
        delay_text,
        text,
        flags=re.IGNORECASE,
    )
    return text


def repair_manifest(
    root: Path,
    request: dict[str, Any],
    *,
    actual_delay_ms: Any | None = None,
) -> dict[str, Any]:
    """Make a manifest report the level and delay actually used.

    Fresh routes use the canonical request delay. Audio repair may pass the
    dominant delay measured from ``segments_ru_final.json`` so a historical
    request/default mismatch cannot survive in Telegram labels.
    """
    settings = values(request)
    path = Path(root) / "output" / "manifest.json"
    if not path.is_file():
        raise RuntimeError(f"Не найден manifest для clean settings: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Повреждён manifest clean route: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Clean manifest не является JSON-объектом.")

    level = float(settings["original_level"])
    if actual_delay_ms is None:
        delay_ms = int(settings["russian_delay_ms"])
        delay_source = "request"
    else:
        delay_ms = russian_delay_ms({"russian_delay_ms": actual_delay_ms})
        delay_source = "segments"
    payload["settings_policy"] = POLICY
    payload["settings_delay_source"] = delay_source
    payload["original_level"] = level
    payload["russian_delay_ms"] = delay_ms
    outputs = payload.get("telegram_outputs")
    if isinstance(outputs, list):
        for item in outputs:
            if isinstance(item, dict) and "label" in item:
                item["label"] = _correct_label(
                    str(item.get("label") or ""),
                    level=level,
                    delay_ms=delay_ms,
                )

    temporary = path.with_suffix(path.suffix + ".settings.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)
    return payload


__all__ = [
    "MAX_RUSSIAN_DELAY_MS",
    "POLICY",
    "original_level",
    "repair_manifest",
    "russian_delay_ms",
    "values",
]
