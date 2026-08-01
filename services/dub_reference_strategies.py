"""Reference preparation strategies selected independently from orchestration."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

REFERENCE_STRATEGY_POLICY = "backend-reference-preparation-strategy-v1"


@dataclass(frozen=True)
class PreparedReferences:
    strategy_id: str
    extended_reference: Path | None
    composite_reference: Path | None
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["extended_reference"] = (
            str(self.extended_reference) if self.extended_reference else ""
        )
        payload["composite_reference"] = (
            str(self.composite_reference) if self.composite_reference else ""
        )
        payload["reference_strategy_policy"] = REFERENCE_STRATEGY_POLICY
        return payload


@runtime_checkable
class ReferencePreparationStrategy(Protocol):
    strategy_id: str

    def prepare(
        self,
        *,
        source_video: Path,
        cues: list[Any],
        duration: float,
        reference_dir: Path,
    ) -> PreparedReferences: ...


class VoxCPM2ReferenceStrategy:
    strategy_id = "voxcpm2-extended-composite"

    def prepare(
        self,
        *,
        source_video: Path,
        cues: list[Any],
        duration: float,
        reference_dir: Path,
    ) -> PreparedReferences:
        from tools.voxcpm2 import generic_short_production as pipeline

        reference_dir = Path(reference_dir)
        reference_dir.mkdir(parents=True, exist_ok=True)
        extended = reference_dir / "extended_reference.wav"
        composite = reference_dir / "composite_reference.wav"
        extended_intervals, composite_intervals = pipeline.reference_intervals(
            cues,
            duration,
        )
        pipeline.build_reference(
            Path(source_video),
            extended_intervals,
            extended,
            target_seconds=min(24.0, max(12.0, duration * 0.45)),
        )
        pipeline.build_reference(
            Path(source_video),
            composite_intervals,
            composite,
            target_seconds=min(21.0, max(10.0, duration * 0.38)),
        )
        return PreparedReferences(
            strategy_id=self.strategy_id,
            extended_reference=extended,
            composite_reference=composite,
            metadata={
                "extended_intervals": len(extended_intervals),
                "composite_intervals": len(composite_intervals),
            },
        )


class NoReferenceStrategy:
    strategy_id = "no-reference"

    def prepare(
        self,
        *,
        source_video: Path,
        cues: list[Any],
        duration: float,
        reference_dir: Path,
    ) -> PreparedReferences:
        del source_video, cues, duration
        Path(reference_dir).mkdir(parents=True, exist_ok=True)
        return PreparedReferences(
            strategy_id=self.strategy_id,
            extended_reference=None,
            composite_reference=None,
            metadata={},
        )


_STRATEGIES: dict[str, ReferencePreparationStrategy] = {}
_BACKEND_STRATEGIES: dict[str, str] = {}


def register_reference_strategy(strategy: ReferencePreparationStrategy) -> None:
    strategy_id = str(getattr(strategy, "strategy_id", "")).casefold().strip()
    if not strategy_id:
        raise ValueError("Reference strategy требует strategy_id.")
    existing = _STRATEGIES.get(strategy_id)
    if existing is not None and existing is not strategy:
        raise RuntimeError(f"Reference strategy уже зарегистрирована: {strategy_id}")
    _STRATEGIES[strategy_id] = strategy


def bind_backend_reference_strategy(backend_id: str, strategy_id: str) -> None:
    backend_id = str(backend_id or "").casefold().strip()
    strategy_id = str(strategy_id or "").casefold().strip()
    if not backend_id or strategy_id not in _STRATEGIES:
        raise ValueError("Нельзя привязать неизвестную reference strategy.")
    _BACKEND_STRATEGIES[backend_id] = strategy_id


def reference_strategy_for_backend(backend_id: str) -> ReferencePreparationStrategy:
    backend_id = str(backend_id or "").casefold().strip()
    strategy_id = _BACKEND_STRATEGIES.get(backend_id, "no-reference")
    return _STRATEGIES[strategy_id]


register_reference_strategy(VoxCPM2ReferenceStrategy())
register_reference_strategy(NoReferenceStrategy())
bind_backend_reference_strategy("voxcpm2", "voxcpm2-extended-composite")
bind_backend_reference_strategy("deterministic-ci", "no-reference")


__all__ = [
    "REFERENCE_STRATEGY_POLICY",
    "NoReferenceStrategy",
    "PreparedReferences",
    "ReferencePreparationStrategy",
    "VoxCPM2ReferenceStrategy",
    "bind_backend_reference_strategy",
    "reference_strategy_for_backend",
    "register_reference_strategy",
]
