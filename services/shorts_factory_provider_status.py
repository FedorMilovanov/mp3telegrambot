"""Typed Factory provider exhaustion; never confuse local circuits with HTTP 503."""

from __future__ import annotations


class FactoryProviderOverloaded(RuntimeError):
    """The strict real-network overload budget is exhausted for one HIGH pass."""

    def __init__(self, message: str, *, stage: str, actual_attempts: int) -> None:
        super().__init__(message)
        self.stage = stage
        self.actual_attempts = actual_attempts


__all__ = ["FactoryProviderOverloaded"]
