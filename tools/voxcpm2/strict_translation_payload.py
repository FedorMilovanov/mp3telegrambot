#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed JSON contracts for Russian translation/editor passes."""
from __future__ import annotations

import math
import re
from typing import Any, Iterable

POLICY = "strict-translation-payload-v1"


def _integer_id(value: Any, *, location: str) -> int:
    if isinstance(value, bool):
        raise RuntimeError(f"{location}: bool нельзя использовать как ID.")
    if isinstance(value, float) and (
        not math.isfinite(value) or not value.is_integer()
    ):
        raise RuntimeError(f"{location}: ID должен быть целым числом, получено {value!r}.")
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError(f"{location}: некорректный ID={value!r}.") from exc
    if result <= 0:
        raise RuntimeError(f"{location}: ID должен быть положительным, получено {result}.")
    return result


def _ordered_unique_ids(values: Iterable[Any], *, location: str) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for index, value in enumerate(values, start=1):
        item_id = _integer_id(value, location=f"{location} #{index}")
        if item_id in seen:
            raise RuntimeError(f"{location}: повторяющийся ID={item_id}.")
        seen.add(item_id)
        result.append(item_id)
    if not result:
        raise RuntimeError(f"{location}: список ID пуст.")
    return result


def _segments(value: Any) -> list[Any]:
    payload = value
    if isinstance(payload, dict):
        if "segments" not in payload:
            raise RuntimeError("Переводчик не вернул поле segments.")
        payload = payload.get("segments")
    if not isinstance(payload, list):
        raise RuntimeError("Переводчик не вернул список segments.")
    return payload


def _validate_exact(value: Any, expected_ids: list[int]) -> list[dict[str, Any]]:
    allowed = set(expected_ids)
    by_id: dict[int, dict[str, Any]] = {}
    payload = _segments(value)
    for position, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"segments[{position}] должен быть JSON-объектом, получено {type(item).__name__}."
            )
        item_id = _integer_id(item.get("id"), location=f"segments[{position}]")
        if item_id not in allowed:
            raise RuntimeError(
                f"segments[{position}]: неожиданный ID={item_id}; разрешены {expected_ids}."
            )
        if item_id in by_id:
            raise RuntimeError(f"Переводчик вернул повторяющийся ID={item_id}.")
        text = re.sub(
            r"\s+",
            " ",
            str(item.get("russian") or item.get("text") or ""),
        ).strip()
        if not text:
            raise RuntimeError(f"Переводчик вернул пустой русский текст для ID={item_id}.")
        by_id[item_id] = {"id": item_id, "russian": text}

    received = set(by_id)
    if received != allowed:
        missing = [item_id for item_id in expected_ids if item_id not in received]
        extra = sorted(received - allowed)
        raise RuntimeError(
            "Нарушены ID перевода: "
            f"ожидались {expected_ids}, получены {sorted(received)}, "
            f"пропущены {missing}, лишние {extra}."
        )
    return [by_id[item_id] for item_id in expected_ids]


def validate_full(
    value: Any,
    source_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(source_groups, list) or not source_groups:
        raise RuntimeError("Исходные группы перевода пусты.")
    expected = _ordered_unique_ids(
        (
            group.get("id") if isinstance(group, dict) else None
            for group in source_groups
        ),
        location="исходные группы",
    )
    return _validate_exact(value, expected)


def validate_subset(value: Any, allowed_ids: Iterable[Any]) -> list[dict[str, Any]]:
    expected = _ordered_unique_ids(allowed_ids, location="разрешённые ID")
    return _validate_exact(value, expected)


__all__ = ["POLICY", "validate_full", "validate_subset"]
