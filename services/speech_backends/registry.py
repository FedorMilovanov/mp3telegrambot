#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small explicit registry for replaceable speech engines."""
from __future__ import annotations

from typing import Iterable

from services.speech_backends.base import SpeechBackend

REGISTRY_POLICY = "explicit-speech-backend-registry-v1"
_REGISTRY: dict[str, SpeechBackend] = {}
_ALIASES: dict[str, str] = {}


def _normalized(value: object) -> str:
    return str(value or "").strip().casefold().replace("_", "-")


def register_backend(backend: SpeechBackend) -> None:
    backend_id = _normalized(getattr(backend, "backend_id", ""))
    if not backend_id:
        raise RuntimeError("Speech backend должен иметь непустой backend_id.")
    existing = _REGISTRY.get(backend_id)
    if existing is not None and existing is not backend:
        raise RuntimeError(f"Speech backend уже зарегистрирован: {backend_id}")
    _REGISTRY[backend_id] = backend
    for raw_alias in (backend_id, *tuple(getattr(backend, "aliases", ()))):
        alias = _normalized(raw_alias)
        if not alias:
            continue
        owner = _ALIASES.get(alias)
        if owner is not None and owner != backend_id:
            raise RuntimeError(f"Alias speech backend уже занят: {alias}")
        _ALIASES[alias] = backend_id


def backend_ids() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def resolve_backend_id(value: object) -> str:
    alias = _normalized(value)
    backend_id = _ALIASES.get(alias)
    if backend_id is None:
        available = ", ".join(backend_ids()) or "—"
        raise RuntimeError(
            f"Неизвестный speech backend: {value!r}. Доступно: {available}."
        )
    return backend_id


def get_backend(value: object) -> SpeechBackend:
    return _REGISTRY[resolve_backend_id(value)]


def unregister_backend(value: object) -> None:
    backend_id = resolve_backend_id(value)
    _REGISTRY.pop(backend_id, None)
    for alias, owner in tuple(_ALIASES.items()):
        if owner == backend_id:
            _ALIASES.pop(alias, None)


def registered_backends() -> Iterable[SpeechBackend]:
    return tuple(_REGISTRY[key] for key in sorted(_REGISTRY))


__all__ = [
    "REGISTRY_POLICY",
    "backend_ids",
    "get_backend",
    "register_backend",
    "registered_backends",
    "resolve_backend_id",
]
