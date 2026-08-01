"""VoxCPM2 adapter whose exact model call is planned and durably auditable."""
from __future__ import annotations

import inspect
import math
from typing import Any

from services.speech_backends.base import BackendGenerationRequest, BackendSessionConfig
from services.speech_backends.execution_plan import (
    BackendGenerationExecutionPlan,
    append_execution_plan_from_environment,
    request_fingerprint,
)
from services.speech_backends.voxcpm2 import (
    ADAPTER_POLICY,
    VoxCPM2Backend,
    VoxCPM2Session,
    _needs_normalization,
)

GENERATION_EXECUTION_CALL_POLICY = "voxcpm2-exact-model-call-v1"


class AuditedVoxCPM2Session(VoxCPM2Session):
    """Expose the final kwargs after all VoxCPM2-specific transformations."""

    last_execution_plan: BackendGenerationExecutionPlan | None = None

    def plan_generation_execution(
        self,
        request: BackendGenerationRequest,
    ) -> BackendGenerationExecutionPlan:
        if not isinstance(request, BackendGenerationRequest):
            raise TypeError(
                "AuditedVoxCPM2Session ожидает BackendGenerationRequest."
            )

        cfg = request.option_float("cfg", default=1.9, low=0.1, high=10.0)
        steps = request.option_int("steps", default=16, low=1, high=256)
        min_len = request.option_int("min_len", default=2, low=1, high=512)
        planned_max_len = request.option_int(
            "max_len",
            default=64,
            low=2,
            high=512,
        )
        if min_len >= planned_max_len:
            raise ValueError("VoxCPM2 min_len должен быть меньше max_len.")

        executed_max_len = min(
            512,
            max(
                planned_max_len,
                int(math.ceil(planned_max_len * 1.45)),
            ),
        )
        parameters = inspect.signature(self._model.generate).parameters
        kwargs: dict[str, Any] = {
            "text": request.text,
            "reference_wav_path": str(request.reference_audio),
            "cfg_value": cfg,
            "inference_timesteps": steps,
            "min_len": min_len,
            "max_len": executed_max_len,
            "normalize": _needs_normalization(request.text),
            "denoise": False,
        }
        optional: dict[str, Any] = {
            "retry_badcase": True,
            "retry_badcase_max_times": 2,
            "retry_badcase_ratio_threshold": 6.0,
            "seed": int(request.seed) if request.seed is not None else None,
        }
        continuation_reference = request.continuation_reference
        if continuation_reference is not None and continuation_reference.is_file():
            optional["prompt_wav_path"] = str(continuation_reference)
            continuation_text = str(request.continuation_text or "").strip()
            if continuation_text:
                if "prompt_text" in parameters:
                    optional["prompt_text"] = continuation_text
                elif "reference_text" in parameters:
                    optional["reference_text"] = continuation_text

        accepted: list[str] = []
        omitted: list[str] = []
        for name, value in optional.items():
            if name in parameters and value is not None:
                kwargs[name] = value
                accepted.append(name)
            else:
                omitted.append(name)

        return BackendGenerationExecutionPlan(
            backend_id="voxcpm2",
            adapter_policy=ADAPTER_POLICY,
            request_fingerprint=request_fingerprint(
                text=request.text,
                reference_audio=request.reference_audio,
                seed=request.seed,
            ),
            planned_max_len=planned_max_len,
            executed_max_len=executed_max_len,
            model_kwargs=kwargs,
            accepted_optional_parameters=tuple(accepted),
            omitted_optional_parameters=tuple(omitted),
        )

    def generate(self, request: BackendGenerationRequest) -> Any:
        plan = self.plan_generation_execution(request)
        self.last_execution_plan = plan
        append_execution_plan_from_environment(plan)
        return self._model.generate(**dict(plan.model_kwargs))


class AuditedVoxCPM2Backend(VoxCPM2Backend):
    """Production VoxCPM2 backend with exact-call execution evidence."""

    def open_session(
        self,
        config: BackendSessionConfig,
    ) -> AuditedVoxCPM2Session:
        session = super().open_session(config)
        if not isinstance(session, VoxCPM2Session):
            raise TypeError("VoxCPM2 backend вернул неизвестный тип сессии.")
        return AuditedVoxCPM2Session(session._model, session.audio_spec)


__all__ = [
    "GENERATION_EXECUTION_CALL_POLICY",
    "AuditedVoxCPM2Backend",
    "AuditedVoxCPM2Session",
]
