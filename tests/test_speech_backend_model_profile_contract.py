from __future__ import annotations

import pytest

from services.speech_backends import (
    DEFAULT_BACKEND_ID,
    DEFAULT_MODEL_PROFILE_ID,
    BackendModelProfileContract,
    DeterministicSpeechBackend,
    ModelOptionSpec,
    ModelProfileContractError,
    SpeechModelConfigurationError,
    SpeechModelProfile,
    default_model_profile,
    deterministic_model_profile_contract,
    get_backend_model_contract,
    register_backend,
    register_backend_model_contract,
    register_model_profile,
    select_production_speech,
    unregister_backend,
    unregister_backend_model_contract,
    unregister_model_profile,
    voxcpm2_model_profile_contract,
)


def _select(profile: SpeechModelProfile):
    register_model_profile(profile)
    try:
        return select_production_speech(
            profile.backend_id,
            profile.profile_id,
            request={},
            default_backend_id=DEFAULT_BACKEND_ID,
            default_model_profile_id=DEFAULT_MODEL_PROFILE_ID,
            required_capabilities=(),
        )
    finally:
        unregister_model_profile(profile.profile_id)


def _vox_profile(
    profile_id: str,
    *,
    option_specs: tuple[ModelOptionSpec, ...],
    backend_defaults: dict[str, object],
    requires_execution_plan_evidence: bool = True,
) -> SpeechModelProfile:
    return SpeechModelProfile(
        profile_id=profile_id,
        backend_id="voxcpm2",
        display_name=profile_id,
        model_family="OpenBMB/VoxCPM2",
        model_revision="contract-test",
        option_specs=option_specs,
        backend_defaults=backend_defaults,
        backend_override_keys=tuple(backend_defaults),
        required_capabilities=(),
        requires_execution_plan_evidence=requires_execution_plan_evidence,
    )


def _required_vox_options() -> tuple[ModelOptionSpec, ...]:
    return (
        ModelOptionSpec("threads", "int", 10, minimum=1, maximum=64),
        ModelOptionSpec("steps", "int", 16, minimum=1, maximum=256),
        ModelOptionSpec("cfg", "float", 1.8, minimum=0.1, maximum=10.0),
        ModelOptionSpec("cache_length", "int", 4096, minimum=2048, maximum=131072),
        ModelOptionSpec("base_seed", "int", 42, minimum=0, maximum=2147483647),
    )


def test_builtin_voxcpm2_profile_matches_adapter_contract() -> None:
    contract = get_backend_model_contract("voxcpm2")
    profile = default_model_profile()

    contract.validate_profile(profile)
    selection = select_production_speech(
        None,
        profile.profile_id,
        request={},
        default_backend_id=DEFAULT_BACKEND_ID,
        default_model_profile_id=DEFAULT_MODEL_PROFILE_ID,
    )

    assert contract == voxcpm2_model_profile_contract()
    assert selection.model_contract is contract
    assert selection.as_dict()["model_contract"]["backend_id"] == "voxcpm2"


def test_adapter_contract_rejects_profile_option_it_does_not_consume() -> None:
    profile = _vox_profile(
        "voxcpm2-unknown-option-test",
        option_specs=(
            *_required_vox_options(),
            ModelOptionSpec("temperature", "float", 0.7, minimum=0.0, maximum=2.0),
        ),
        backend_defaults={"vox_archive": "archive", "cpu_venv": "venv"},
    )

    with pytest.raises(SpeechModelConfigurationError, match="не потребляет: temperature"):
        _select(profile)


def test_adapter_contract_rejects_missing_required_option() -> None:
    profile = _vox_profile(
        "voxcpm2-missing-option-test",
        option_specs=tuple(
            spec for spec in _required_vox_options() if spec.name != "cache_length"
        ),
        backend_defaults={"vox_archive": "archive", "cpu_venv": "venv"},
    )

    with pytest.raises(SpeechModelConfigurationError, match="cache_length"):
        _select(profile)


def test_adapter_contract_rejects_unknown_and_missing_backend_config() -> None:
    unknown = _vox_profile(
        "voxcpm2-unknown-config-test",
        option_specs=_required_vox_options(),
        backend_defaults={
            "vox_archive": "archive",
            "cpu_venv": "venv",
            "weights_cache": "cache",
        },
    )
    missing = _vox_profile(
        "voxcpm2-missing-config-test",
        option_specs=_required_vox_options(),
        backend_defaults={"vox_archive": "archive"},
    )

    with pytest.raises(SpeechModelConfigurationError, match="weights_cache"):
        _select(unknown)
    with pytest.raises(SpeechModelConfigurationError, match="cpu_venv"):
        _select(missing)


def test_adapter_without_profile_contract_fails_before_resolution() -> None:
    backend = DeterministicSpeechBackend()
    backend.backend_id = "deterministic-without-contract"
    backend.aliases = (backend.backend_id,)
    profile = SpeechModelProfile(
        profile_id="deterministic-without-contract-profile",
        backend_id=backend.backend_id,
        display_name="No contract fixture",
        model_family="deterministic",
        model_revision="v1",
        option_specs=(ModelOptionSpec("sample_rate", "int", 22050),),
        backend_defaults={"deterministic_archive": "."},
        backend_override_keys=("deterministic_archive",),
        required_capabilities=(),
    )
    register_backend(backend)
    register_model_profile(profile)
    try:
        with pytest.raises(SpeechModelConfigurationError, match="не имеет model-profile contract"):
            select_production_speech(
                backend.backend_id,
                profile.profile_id,
                request={},
                default_backend_id=DEFAULT_BACKEND_ID,
                default_model_profile_id=DEFAULT_MODEL_PROFILE_ID,
                required_capabilities=(),
            )
    finally:
        unregister_model_profile(profile.profile_id)
        unregister_backend(backend.backend_id)


def test_adapter_contract_rejects_unsupported_execution_evidence() -> None:
    backend = DeterministicSpeechBackend()
    backend.backend_id = "deterministic-evidence-test"
    backend.aliases = (backend.backend_id,)
    contract = deterministic_model_profile_contract(backend.backend_id)
    profile = SpeechModelProfile(
        profile_id="deterministic-evidence-profile",
        backend_id=backend.backend_id,
        display_name="Evidence fixture",
        model_family="deterministic",
        model_revision="v1",
        option_specs=(ModelOptionSpec("sample_rate", "int", 22050),),
        backend_defaults={"deterministic_archive": "."},
        backend_override_keys=("deterministic_archive",),
        required_capabilities=(),
        requires_execution_plan_evidence=True,
    )
    register_backend(backend)
    register_backend_model_contract(contract)
    register_model_profile(profile)
    try:
        with pytest.raises(SpeechModelConfigurationError, match="не объявил такую поддержку"):
            select_production_speech(
                backend.backend_id,
                profile.profile_id,
                request={},
                default_backend_id=DEFAULT_BACKEND_ID,
                default_model_profile_id=DEFAULT_MODEL_PROFILE_ID,
                required_capabilities=(),
            )
    finally:
        unregister_model_profile(profile.profile_id)
        unregister_backend_model_contract(backend.backend_id)
        unregister_backend(backend.backend_id)


def test_contract_registry_rejects_conflicting_redefinition() -> None:
    first = BackendModelProfileContract(
        backend_id="contract-registry-test",
        option_keys=("sample_rate",),
    )
    conflicting = BackendModelProfileContract(
        backend_id="contract-registry-test",
        option_keys=("sample_rate", "temperature"),
    )
    register_backend_model_contract(first)
    try:
        register_backend_model_contract(
            BackendModelProfileContract(
                backend_id="contract-registry-test",
                option_keys=("sample_rate",),
            )
        )
        with pytest.raises(RuntimeError, match="уже зарегистрирован"):
            register_backend_model_contract(conflicting)
        assert get_backend_model_contract(first.backend_id) == first
    finally:
        unregister_backend_model_contract(first.backend_id)


def test_contract_constructor_rejects_invalid_key_sets() -> None:
    with pytest.raises(ValueError, match="required_option_keys"):
        BackendModelProfileContract(
            backend_id="invalid-required-test",
            option_keys=("sample_rate",),
            required_option_keys=("temperature",),
        )
    with pytest.raises(ValueError, match="пересекаются"):
        BackendModelProfileContract(
            backend_id="invalid-overlap-test",
            option_keys=("sample_rate",),
            backend_config_keys=("sample_rate",),
        )
    with pytest.raises(ModelProfileContractError, match="принадлежит backend"):
        voxcpm2_model_profile_contract().validate_profile(
            SpeechModelProfile(
                profile_id="wrong-backend-profile",
                backend_id="deterministic-ci",
                display_name="Wrong backend",
                model_family="deterministic",
                model_revision="v1",
                required_capabilities=(),
            )
        )
