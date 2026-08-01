#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-closed contracts between TTS adapters and concrete model profiles."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MODEL_PROFILE_CONTRACT_POLICY = "speech-backend-model-profile-contract-v1"


def _normalized_id(value: object, *, field_name: str) -> str:
    result = str(value or "").strip().casefold().replace("_", "-")
    if not 3 <= len(result) <= 96:
        raise ValueError(f"{field_name} должен содержать 3..96 символов.")
    if not result[0].isalnum() or not result[-1].isalnum():
        raise ValueError(f"{field_name} должен начинаться и заканчиваться буквой или цифрой.")
    if any(not (char.isascii() and (char.isalnum() or char == "-")) for char in result):
        raise ValueError(f"{field_name} содержит запрещённые символы.")
    if "--" in result:
        raise ValueError(f"{field_name} не может содержать двойной дефис.")
    return result


def _key(value: object, *, field_name: str) -> str:
    result = str(value or "").strip()
    if not 1 <= len(result) <= 64:
        raise ValueError(f"{field_name} должен содержать 1..64 символа.")
    if not result[0].isalpha():
        raise ValueError(f"{field_name} должен начинаться с буквы.")
    if any(not (char.isascii() and (char.isalnum() or char == "_")) for char in result):
        raise ValueError(f"Некорректный {field_name}: {result!r}.")
    return result


def _keys(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    result = tuple(_key(value, field_name=field_name) for value in values)
    if len(result) != len(set(result)):
        raise ValueError(f"{field_name} содержит дубли.")
    return result


class ModelProfileContractError(RuntimeError):
    """Raised when an adapter/profile pair cannot be executed faithfully."""


@dataclass(frozen=True)
class BackendModelProfileContract:
    """Configuration surface explicitly consumed by one backend adapter."""

    backend_id: str
    option_keys: tuple[str, ...]
    required_option_keys: tuple[str, ...] = ()
    backend_config_keys: tuple[str, ...] = ()
    required_backend_config_keys: tuple[str, ...] = ()
    execution_plan_evidence_supported: bool = False

    def __post_init__(self) -> None:
        backend_id = _normalized_id(self.backend_id, field_name="backend_id")
        option_keys = _keys(self.option_keys, field_name="option_keys")
        required_option_keys = _keys(
            self.required_option_keys,
            field_name="required_option_keys",
        )
        backend_config_keys = _keys(
            self.backend_config_keys,
            field_name="backend_config_keys",
        )
        required_backend_config_keys = _keys(
            self.required_backend_config_keys,
            field_name="required_backend_config_keys",
        )
        unknown_required_options = sorted(
            set(required_option_keys) - set(option_keys)
        )
        if unknown_required_options:
            raise ValueError(
                "required_option_keys отсутствуют в option_keys: "
                + ", ".join(unknown_required_options)
            )
        unknown_required_config = sorted(
            set(required_backend_config_keys) - set(backend_config_keys)
        )
        if unknown_required_config:
            raise ValueError(
                "required_backend_config_keys отсутствуют в backend_config_keys: "
                + ", ".join(unknown_required_config)
            )
        overlap = sorted(set(option_keys) & set(backend_config_keys))
        if overlap:
            raise ValueError(
                "option_keys и backend_config_keys пересекаются: "
                + ", ".join(overlap)
            )
        object.__setattr__(self, "backend_id", backend_id)
        object.__setattr__(self, "option_keys", option_keys)
        object.__setattr__(self, "required_option_keys", required_option_keys)
        object.__setattr__(self, "backend_config_keys", backend_config_keys)
        object.__setattr__(
            self,
            "required_backend_config_keys",
            required_backend_config_keys,
        )

    def validate_profile(self, profile: Any) -> None:
        profile_id = str(getattr(profile, "profile_id", "") or "").strip()
        profile_backend_id = str(
            getattr(profile, "backend_id", "") or ""
        ).casefold().strip()
        if profile_backend_id != self.backend_id:
            raise ModelProfileContractError(
                f"TTS profile {profile_id or '—'} принадлежит backend="
                f"{profile_backend_id or '—'}, а contract — backend={self.backend_id}."
            )

        profile_options = tuple(
            str(getattr(spec, "name", "") or "").strip()
            for spec in tuple(getattr(profile, "option_specs", ()) or ())
        )
        unknown_options = sorted(set(profile_options) - set(self.option_keys))
        if unknown_options:
            raise ModelProfileContractError(
                f"TTS profile {profile_id} объявляет options, которые adapter "
                f"{self.backend_id} не потребляет: {', '.join(unknown_options)}."
            )
        missing_options = sorted(
            set(self.required_option_keys) - set(profile_options)
        )
        if missing_options:
            raise ModelProfileContractError(
                f"TTS profile {profile_id} не объявляет обязательные options adapter "
                f"{self.backend_id}: {', '.join(missing_options)}."
            )

        profile_config = tuple(
            str(key) for key in dict(getattr(profile, "backend_defaults", {}) or {})
        )
        unknown_config = sorted(
            set(profile_config) - set(self.backend_config_keys)
        )
        if unknown_config:
            raise ModelProfileContractError(
                f"TTS profile {profile_id} объявляет backend config, который adapter "
                f"{self.backend_id} не потребляет: {', '.join(unknown_config)}."
            )
        missing_config = sorted(
            set(self.required_backend_config_keys) - set(profile_config)
        )
        if missing_config:
            raise ModelProfileContractError(
                f"TTS profile {profile_id} не объявляет обязательный backend config "
                f"adapter {self.backend_id}: {', '.join(missing_config)}."
            )

        requires_evidence = bool(
            getattr(profile, "requires_execution_plan_evidence", False)
        )
        if requires_evidence and not self.execution_plan_evidence_supported:
            raise ModelProfileContractError(
                f"TTS profile {profile_id} требует execution-plan evidence, но adapter "
                f"{self.backend_id} не объявил такую поддержку."
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend_id": self.backend_id,
            "option_keys": list(self.option_keys),
            "required_option_keys": list(self.required_option_keys),
            "backend_config_keys": list(self.backend_config_keys),
            "required_backend_config_keys": list(
                self.required_backend_config_keys
            ),
            "execution_plan_evidence_supported": bool(
                self.execution_plan_evidence_supported
            ),
            "profile_contract_policy": MODEL_PROFILE_CONTRACT_POLICY,
        }


_CONTRACTS: dict[str, BackendModelProfileContract] = {}


def register_backend_model_contract(
    contract: BackendModelProfileContract,
) -> None:
    if not isinstance(contract, BackendModelProfileContract):
        raise TypeError(
            "register_backend_model_contract ожидает BackendModelProfileContract."
        )
    existing = _CONTRACTS.get(contract.backend_id)
    if existing is not None and existing is not contract:
        if existing != contract:
            raise RuntimeError(
                f"Model profile contract уже зарегистрирован: {contract.backend_id}"
            )
        return
    _CONTRACTS[contract.backend_id] = contract


def get_backend_model_contract(value: object) -> BackendModelProfileContract:
    backend_id = _normalized_id(value, field_name="backend_id")
    contract = _CONTRACTS.get(backend_id)
    if contract is None:
        available = ", ".join(sorted(_CONTRACTS)) or "—"
        raise ModelProfileContractError(
            f"Backend {backend_id} не имеет model-profile contract. "
            f"Доступно: {available}."
        )
    return contract


def backend_model_contract_ids() -> tuple[str, ...]:
    return tuple(sorted(_CONTRACTS))


def unregister_backend_model_contract(value: object) -> None:
    backend_id = _normalized_id(value, field_name="backend_id")
    _CONTRACTS.pop(backend_id, None)


__all__ = [
    "MODEL_PROFILE_CONTRACT_POLICY",
    "BackendModelProfileContract",
    "ModelProfileContractError",
    "backend_model_contract_ids",
    "get_backend_model_contract",
    "register_backend_model_contract",
    "unregister_backend_model_contract",
]
