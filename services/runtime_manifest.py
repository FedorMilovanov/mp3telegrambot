"""Declarative, fail-closed runtime composition for the production bot.

The entry point must not silently continue with an arbitrary subset of safety
adapters. This module owns one ordered feature manifest, records installation
evidence and stops startup when a required feature cannot be installed.
"""
from __future__ import annotations

import importlib
import logging
import threading
from dataclasses import dataclass
from enum import Enum
from types import ModuleType
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)

RUNTIME_MANIFEST_POLICY = "declarative-runtime-composition-v1"


class RuntimePhase(str, Enum):
    PRE_MAIN = "pre-main"
    POST_MAIN = "post-main"


class RuntimeFeatureState(str, Enum):
    PENDING = "pending"
    INSTALLED = "installed"
    SKIPPED = "skipped"
    FAILED = "failed"


class RuntimeBootstrapError(RuntimeError):
    """Raised when a required runtime feature cannot be installed."""


@dataclass(frozen=True)
class RuntimeFeature:
    feature_id: str
    module: str
    installer: str
    phase: RuntimePhase
    required: bool = True
    requires_main: bool = False
    dependencies: tuple[str, ...] = ()
    false_is_failure: bool = False

    def __post_init__(self) -> None:
        feature_id = str(self.feature_id or "").strip()
        module = str(self.module or "").strip()
        installer = str(self.installer or "").strip()
        if not feature_id or not module or not installer:
            raise ValueError("RuntimeFeature requires non-empty id, module and installer.")
        object.__setattr__(self, "feature_id", feature_id)
        object.__setattr__(self, "module", module)
        object.__setattr__(self, "installer", installer)
        object.__setattr__(
            self,
            "dependencies",
            tuple(str(item).strip() for item in self.dependencies if str(item).strip()),
        )


@dataclass(frozen=True)
class RuntimeFeatureResult:
    feature_id: str
    phase: RuntimePhase
    required: bool
    state: RuntimeFeatureState
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "phase": self.phase.value,
            "required": self.required,
            "state": self.state.value,
            "detail": str(self.detail),
        }


class RuntimeManifest:
    """Install one explicit feature graph and retain auditable evidence."""

    def __init__(self, features: Iterable[RuntimeFeature]) -> None:
        ordered = tuple(features)
        ids = [feature.feature_id for feature in ordered]
        if len(ids) != len(set(ids)):
            raise ValueError("Runtime feature ids must be unique.")
        known = set(ids)
        missing = {
            dependency
            for feature in ordered
            for dependency in feature.dependencies
            if dependency not in known
        }
        if missing:
            raise ValueError(f"Unknown runtime feature dependencies: {sorted(missing)}")
        self._features = ordered
        self._results: dict[str, RuntimeFeatureResult] = {
            feature.feature_id: RuntimeFeatureResult(
                feature_id=feature.feature_id,
                phase=feature.phase,
                required=feature.required,
                state=RuntimeFeatureState.PENDING,
            )
            for feature in ordered
        }
        self._lock = threading.RLock()

    def _dependency_failure(self, feature: RuntimeFeature) -> str | None:
        for dependency in feature.dependencies:
            state = self._results[dependency].state
            if state is not RuntimeFeatureState.INSTALLED:
                return f"dependency {dependency!r} is {state.value}"
        return None

    def _record(
        self,
        feature: RuntimeFeature,
        state: RuntimeFeatureState,
        detail: str = "",
    ) -> RuntimeFeatureResult:
        result = RuntimeFeatureResult(
            feature_id=feature.feature_id,
            phase=feature.phase,
            required=feature.required,
            state=state,
            detail=str(detail),
        )
        self._results[feature.feature_id] = result
        return result

    def install_phase(
        self,
        phase: RuntimePhase,
        *,
        main_module: ModuleType | None = None,
    ) -> tuple[RuntimeFeatureResult, ...]:
        with self._lock:
            completed: list[RuntimeFeatureResult] = []
            for feature in self._features:
                if feature.phase is not phase:
                    continue
                current = self._results[feature.feature_id]
                if current.state is RuntimeFeatureState.INSTALLED:
                    completed.append(current)
                    continue

                dependency_failure = self._dependency_failure(feature)
                if dependency_failure:
                    state = (
                        RuntimeFeatureState.FAILED
                        if feature.required
                        else RuntimeFeatureState.SKIPPED
                    )
                    result = self._record(feature, state, dependency_failure)
                    completed.append(result)
                    if feature.required:
                        raise RuntimeBootstrapError(
                            f"Required runtime feature {feature.feature_id!r} blocked: "
                            f"{dependency_failure}"
                        )
                    continue

                if feature.requires_main and main_module is None:
                    detail = "main module is required but was not supplied"
                    result = self._record(feature, RuntimeFeatureState.FAILED, detail)
                    completed.append(result)
                    if feature.required:
                        raise RuntimeBootstrapError(
                            f"Required runtime feature {feature.feature_id!r} failed: {detail}"
                        )
                    continue

                try:
                    module = importlib.import_module(feature.module)
                    installer = getattr(module, feature.installer)
                    outcome = installer(main_module) if feature.requires_main else installer()
                    if feature.false_is_failure and outcome is False:
                        raise RuntimeError("installer returned False")
                except Exception as exc:
                    result = self._record(
                        feature,
                        RuntimeFeatureState.FAILED,
                        f"{type(exc).__name__}: {exc}",
                    )
                    completed.append(result)
                    if feature.required:
                        raise RuntimeBootstrapError(
                            f"Required runtime feature {feature.feature_id!r} failed: "
                            f"{type(exc).__name__}: {exc}"
                        ) from exc
                    logger.warning(
                        "Optional runtime feature %s was not installed: %s",
                        feature.feature_id,
                        exc,
                    )
                else:
                    result = self._record(
                        feature,
                        RuntimeFeatureState.INSTALLED,
                        f"{feature.module}.{feature.installer}",
                    )
                    completed.append(result)
            return tuple(completed)

    def require_ready(self) -> None:
        failures = [
            result
            for result in self._results.values()
            if result.required and result.state is not RuntimeFeatureState.INSTALLED
        ]
        if failures:
            summary = "; ".join(
                f"{result.feature_id}={result.state.value}({result.detail})"
                for result in failures
            )
            raise RuntimeBootstrapError(f"Runtime manifest is not ready: {summary}")

    def snapshot(self) -> Mapping[str, RuntimeFeatureResult]:
        with self._lock:
            return dict(self._results)

    def as_dict(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        required_ready = all(
            not result.required or result.state is RuntimeFeatureState.INSTALLED
            for result in snapshot.values()
        )
        return {
            "policy": RUNTIME_MANIFEST_POLICY,
            "required_ready": required_ready,
            "features": {
                feature_id: result.as_dict()
                for feature_id, result in snapshot.items()
            },
        }

    def status_lines(self) -> tuple[str, ...]:
        payload = self.as_dict()
        lines = [
            f"runtime={payload['policy']}",
            f"required_ready={payload['required_ready']}",
        ]
        for feature_id, result in payload["features"].items():
            marker = "required" if result["required"] else "optional"
            detail = f" — {result['detail']}" if result["detail"] else ""
            lines.append(
                f"{feature_id}: {result['state']} ({marker}, {result['phase']}){detail}"
            )
        return tuple(lines)


DEFAULT_RUNTIME_FEATURES = (
    RuntimeFeature(
        "singleton",
        "services.project_runtime_hardening",
        "acquire_early_singleton",
        RuntimePhase.PRE_MAIN,
        false_is_failure=True,
    ),
    RuntimeFeature(
        "local-bot-api",
        "services.local_botapi_required",
        "require_local_bot_api",
        RuntimePhase.PRE_MAIN,
    ),
    RuntimeFeature(
        "livedub-info-guard",
        "services.livedub_info_guard",
        "install_livedub_info_guard",
        RuntimePhase.PRE_MAIN,
        required=False,
    ),
    RuntimeFeature(
        "livedub-info-presentation",
        "services.livedub_info_presentation",
        "install_livedub_info_presentation",
        RuntimePhase.PRE_MAIN,
        required=False,
        dependencies=("livedub-info-guard",),
    ),
    RuntimeFeature(
        "livedub-long-qa",
        "services.livedub_long_qa",
        "install_livedub_long_qa",
        RuntimePhase.PRE_MAIN,
    ),
    RuntimeFeature(
        "livedub-qa-trust",
        "services.livedub_qa_trust",
        "install_livedub_qa_trust",
        RuntimePhase.PRE_MAIN,
    ),
    RuntimeFeature(
        "livedub-delivery-hardening",
        "services.livedub_delivery_hardening",
        "install_livedub_delivery_hardening",
        RuntimePhase.PRE_MAIN,
    ),
    RuntimeFeature(
        "gemini-startup-diagnostics",
        "services.gemini_startup_diagnostics",
        "install_gemini_startup_diagnostics",
        RuntimePhase.POST_MAIN,
        required=False,
        requires_main=True,
    ),
    RuntimeFeature(
        "livedub-help",
        "services.livedub_help_runtime",
        "install_livedub_help_runtime",
        RuntimePhase.POST_MAIN,
        required=False,
        requires_main=True,
    ),
    RuntimeFeature(
        "livedub-output-policy",
        "services.livedub_output_policy",
        "install_livedub_output_policy",
        RuntimePhase.POST_MAIN,
    ),
    RuntimeFeature(
        "livedub-publication",
        "services.livedub_publication",
        "install_livedub_publication",
        RuntimePhase.POST_MAIN,
        required=False,
    ),
    RuntimeFeature(
        "livedub-publication-diagnostics",
        "services.livedub_publication_error_diagnostics",
        "install_livedub_publication_error_diagnostics",
        RuntimePhase.POST_MAIN,
        required=False,
        dependencies=("livedub-publication",),
    ),
    RuntimeFeature(
        "livedub-audio-companion",
        "services.livedub_audio_companion",
        "install_livedub_audio_companion",
        RuntimePhase.POST_MAIN,
    ),
    RuntimeFeature(
        "livedub-audio-cache-recovery",
        "services.livedub_audio_cache_recovery",
        "install_livedub_audio_cache_recovery",
        RuntimePhase.POST_MAIN,
        dependencies=("livedub-audio-companion",),
    ),
    RuntimeFeature(
        "livedub-audio-quality",
        "services.livedub_audio_quality_guard",
        "install_livedub_audio_quality_guard",
        RuntimePhase.POST_MAIN,
        dependencies=("livedub-audio-companion",),
    ),
    RuntimeFeature(
        "livedub-ru-provenance",
        "services.livedub_ru_provenance",
        "install_livedub_ru_provenance",
        RuntimePhase.POST_MAIN,
    ),
    RuntimeFeature(
        "livedub-new-delivery-atomicity",
        "services.livedub_new_delivery_atomicity",
        "install_livedub_new_delivery_atomicity",
        RuntimePhase.POST_MAIN,
        dependencies=("livedub-audio-companion",),
    ),
    RuntimeFeature(
        "livedub-cached-delivery-atomicity",
        "services.livedub_cached_delivery_atomicity",
        "install_livedub_cached_delivery_atomicity",
        RuntimePhase.POST_MAIN,
    ),
    RuntimeFeature(
        "livedub-audio-dedupe",
        "services.livedub_audio_dedupe",
        "install_livedub_audio_dedupe",
        RuntimePhase.POST_MAIN,
    ),
    RuntimeFeature(
        "livedub-audio-dedupe-hardening",
        "services.livedub_output_policy",
        "harden_livedub_audio_dedupe",
        RuntimePhase.POST_MAIN,
        dependencies=("livedub-output-policy", "livedub-audio-dedupe"),
    ),
    RuntimeFeature(
        "livedub-deep-audit",
        "services.livedub_deep_audit",
        "install_livedub_deep_audit",
        RuntimePhase.POST_MAIN,
    ),
    RuntimeFeature(
        "livedub-dual-audio-policy",
        "services.livedub_dual_audio_policy",
        "install_livedub_dual_audio_policy",
        RuntimePhase.POST_MAIN,
        dependencies=("livedub-audio-companion",),
    ),
    RuntimeFeature(
        "project-runtime-hardening",
        "services.project_runtime_hardening",
        "install_project_runtime_hardening",
        RuntimePhase.POST_MAIN,
        requires_main=True,
    ),
    RuntimeFeature(
        "shorts-factory-max",
        "services.shorts_factory_runtime",
        "install_shorts_factory_mode",
        RuntimePhase.POST_MAIN,
        requires_main=True,
        false_is_failure=True,
    ),
    RuntimeFeature(
        "shorts-factory-overload-editorial-polish",
        "services.shorts_factory_overload_editorial_polish",
        "install_shorts_factory_overload_editorial_polish",
        RuntimePhase.POST_MAIN,
        dependencies=("shorts-factory-max",),
        false_is_failure=True,
    ),
    RuntimeFeature(
        "dub-studio-runtime",
        "services.dub_studio_runtime",
        "install_dub_studio_runtime",
        RuntimePhase.POST_MAIN,
    ),
    RuntimeFeature(
        "dub-title-policy",
        "services.dub_title_policy",
        "install_dub_title_policy",
        RuntimePhase.POST_MAIN,
    ),
    RuntimeFeature(
        "restart-state-runtime",
        "services.restart_state_runtime",
        "install_restart_state_runtime",
        RuntimePhase.POST_MAIN,
        requires_main=True,
    ),
)

_DEFAULT_MANIFEST = RuntimeManifest(DEFAULT_RUNTIME_FEATURES)


def bootstrap_pre_main() -> tuple[RuntimeFeatureResult, ...]:
    return _DEFAULT_MANIFEST.install_phase(RuntimePhase.PRE_MAIN)


def bootstrap_post_main(main_module: ModuleType) -> tuple[RuntimeFeatureResult, ...]:
    return _DEFAULT_MANIFEST.install_phase(
        RuntimePhase.POST_MAIN,
        main_module=main_module,
    )


def require_runtime_ready() -> None:
    _DEFAULT_MANIFEST.require_ready()


def runtime_manifest_payload() -> dict[str, Any]:
    return _DEFAULT_MANIFEST.as_dict()


def runtime_manifest_status_lines() -> tuple[str, ...]:
    return _DEFAULT_MANIFEST.status_lines()


__all__ = [
    "DEFAULT_RUNTIME_FEATURES",
    "RUNTIME_MANIFEST_POLICY",
    "RuntimeBootstrapError",
    "RuntimeFeature",
    "RuntimeFeatureResult",
    "RuntimeFeatureState",
    "RuntimeManifest",
    "RuntimePhase",
    "bootstrap_post_main",
    "bootstrap_pre_main",
    "require_runtime_ready",
    "runtime_manifest_payload",
    "runtime_manifest_status_lines",
]
