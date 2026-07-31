from __future__ import annotations

from pathlib import Path

from services.speech_backends import (
    BACKEND_ENVIRONMENT_POLICY,
    BackendAudioSpec,
    BackendCapabilities,
    BackendIdentity,
    BackendProcessEnvironment,
    BackendRuntimePaths,
    default_backend,
    register_backend,
    get_backend,
    unregister_backend,
)
from services.speech_backends.voxcpm2 import VoxCPM2Session


ROOT = Path(__file__).resolve().parents[1]


def _runtime(request: dict[str, str]):
    return default_backend().runtime_paths(ROOT, request)


def test_backend_owns_process_environment_policy() -> None:
    backend = default_backend()
    base = {
        "CUDA_VISIBLE_DEVICES": "0",
        "VOXCPM_RESCUE_RENDERER": "stale",
        "TRANSFORMERS_OFFLINE": "1",
    }

    policy = backend.process_environment(
        {"threads": 12},
        base_environment=base,
    )
    environment = policy.as_dict(base)

    assert BACKEND_ENVIRONMENT_POLICY == "speech-backend-process-environment-v1"
    assert environment["CUDA_VISIBLE_DEVICES"] == "-1"
    assert environment["OMP_NUM_THREADS"] == "12"
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert "VOXCPM_RESCUE_RENDERER" not in environment
    assert "TRANSFORMERS_OFFLINE" not in environment
    assert policy.as_metadata()["environment_policy"] == BACKEND_ENVIRONMENT_POLICY


def test_voxcpm2_session_owns_model_generate_kwargs() -> None:
    class FakeModel:
        def __init__(self) -> None:
            self.kwargs = None

        def generate(
            self,
            text,
            reference_wav_path,
            cfg_value,
            inference_timesteps,
            min_len,
            max_len,
            normalize,
            denoise,
            retry_badcase=False,
            retry_badcase_max_times=0,
            retry_badcase_ratio_threshold=0.0,
            seed=None,
        ):
            self.kwargs = {
                "text": text,
                "reference_wav_path": reference_wav_path,
                "cfg_value": cfg_value,
                "inference_timesteps": inference_timesteps,
                "min_len": min_len,
                "max_len": max_len,
                "normalize": normalize,
                "denoise": denoise,
                "retry_badcase": retry_badcase,
                "retry_badcase_max_times": retry_badcase_max_times,
                "retry_badcase_ratio_threshold": retry_badcase_ratio_threshold,
                "seed": seed,
            }
            return "fake-wav"

    model = FakeModel()
    session = VoxCPM2Session(
        model,
        BackendAudioSpec(
            encode_sample_rate=16000,
            output_sample_rate=48000,
            seconds_per_step=0.08,
            cache_length=4096,
        ),
    )

    result = session.generate(
        text="В 2026 году грядёт перемена.",
        reference=Path("reference.wav"),
        cfg=1.8,
        steps=16,
        min_len=2,
        max_len=40,
        seed=42,
    )

    assert result == "fake-wav"
    assert model.kwargs["text"].startswith("В 2026")
    assert model.kwargs["reference_wav_path"] == "reference.wav"
    assert model.kwargs["normalize"] is True
    assert model.kwargs["cfg_value"] == 1.8
    assert model.kwargs["inference_timesteps"] == 16
    assert model.kwargs["seed"] == 42


def test_backend_owns_renderer_and_master_command_shapes() -> None:
    runtime = _runtime({"translation_mode": "direct"})
    backend = default_backend()
    render = backend.build_renderer_command(
        runtime,
        values={
            "extended_reference": "extended.wav",
            "composite_reference": "composite.wav",
            "segments_json": "segments.json",
            "segment_work": "segment_work",
            "timeline": "timeline.wav",
            "threads": "10",
            "steps": "16",
            "cfg": "1.8",
            "cache_length": "4096",
            "duration": "12.0",
            "base_seed": "42",
        },
    )
    master = backend.build_master_command(
        runtime,
        values={
            "source": "source.mp4",
            "timeline": "timeline.wav",
            "master_work": "master_work",
            "final_mixed": "mixed.mp4",
            "final_russian": "russian.mp4",
            "original_level": "0.18",
            "target_i": "-16.0",
            "target_lra": "8.0",
            "target_tp": "-1.5",
        },
    )

    assert render[0] == str(runtime.cpu_python)
    assert render[1] == str(runtime.renderer_entrypoint)
    assert "--speech-backend" in render
    assert render[render.index("--speech-backend") + 1] == "voxcpm2"
    assert master[1] == str(runtime.master_entrypoint)
    assert master[master.index("--russian-only-video") + 1] == "russian.mp4"
    assert runtime.master_module == "tools.voxcpm2.master_monolithic_mix"


def test_non_direct_mode_keeps_backend_boundary_for_legacy_master() -> None:
    runtime = _runtime({"translation_mode": "gemini"})
    assert runtime.backend_id == "voxcpm2"
    assert runtime.master_module.endswith("master_constant_mix")
    assert runtime.master_entrypoint.name == "master_constant_mix.py"


def test_source_prosody_policy_is_model_independent_and_diagnostic_only() -> None:
    from tools.voxcpm2 import source_prosody_policy

    segment = source_prosody_policy.mark_diagnostic_only(
        {
            "text": "Текст.",
            "source_prosody": {"f0_median": 240.0},
        }
    )
    ranked = source_prosody_policy.ranking_view(segment)

    assert source_prosody_policy.is_diagnostic_only(segment) is True
    assert "source_prosody" not in ranked
    assert segment["source_prosody"]["f0_median"] == 240.0


def test_registry_can_register_a_future_backend_without_core_changes() -> None:
    class FutureBackend:
        backend_id = "future-test-engine"
        aliases = ("future-test",)
        adapter_policy = "future-test-adapter-v1"

        def capabilities(self) -> BackendCapabilities:
            return BackendCapabilities(True, True, True, True, False, True, True)

        def process_environment(self, request: dict, *, base_environment=None):
            return BackendProcessEnvironment(
                backend_id=self.backend_id,
                set_values=(("FUTURE_THREADS", str(request.get("threads", 1))),),
                removed_keys=(),
            )

        def open_session(self, model_path: Path, *, cache_length: int, torch_module):
            raise RuntimeError("future session is outside this contract test")

        def discover_model(self, archive_root: Path) -> Path:
            return Path(archive_root) / "future-model"

        def identity(self, archive_root: Path) -> BackendIdentity:
            return BackendIdentity(
                backend_id=self.backend_id,
                family="test",
                adapter_policy=self.adapter_policy,
                model_path=str(self.discover_model(archive_root)),
                runtime_module="future_engine",
                parameter_schema=("seed",),
                output_contract="wav-v1",
            )

        def runtime_paths(self, repo_root: Path, request: dict):
            return BackendRuntimePaths(
                backend_id=self.backend_id,
                repo_root=repo_root,
                cpu_python=Path("future-python"),
                archive_root=Path("future-archive"),
                renderer_entrypoint=repo_root / "future-render.py",
                master_entrypoint=repo_root / "future-master.py",
                import_modules=("future_engine",),
                renderer_module="future_engine.renderer",
                master_module="future_engine.master",
                final_qa_module="tools.voxcpm2.final_media_qa",
            )

        def build_renderer_command(self, runtime, *, values):
            return [str(runtime.cpu_python), str(runtime.renderer_entrypoint)]

        def build_master_command(self, runtime, *, values):
            return [str(runtime.cpu_python), str(runtime.master_entrypoint)]

    backend = FutureBackend()
    register_backend(backend)
    try:
        assert get_backend("future-test").backend_id == "future-test-engine"
    finally:
        unregister_backend("future-test")


def test_direct_candidate_ranking_strips_source_prosody(monkeypatch) -> None:
    from tools.voxcpm2 import direct_max_quality_cli as cli

    monkeypatch.setattr(
        cli,
        "_legacy_source_prosody_penalty",
        lambda _candidate, segment: 100.0 if "source_prosody" in segment else 3.0,
    )
    monkeypatch.setattr(cli.direct_monolith_contract, "evaluate_candidate", lambda *_args: {})
    monkeypatch.setattr(cli.direct_monolith_contract, "candidate_penalty", lambda _candidate: 0.0)
    monkeypatch.setattr(
        cli.russian_pronunciation,
        "prepare_segment",
        lambda segment: {"display_text": segment.get("text", "")},
    )
    monkeypatch.setattr(
        cli.russian_pronunciation,
        "variant_for_attempt",
        lambda *_args: {"variant_index": 0},
    )

    candidate: dict = {"attempt": 1}
    segment = cli.source_prosody_policy.mark_diagnostic_only(
        {"text": "Текст.", "source_prosody": {"f0_median": 300.0}}
    )
    penalty = cli.source_prosody_penalty(candidate, segment)

    assert penalty == 3.0
    assert candidate["source_prosody_match"]["source_prosody_ranking_enabled"] is False


def test_generic_project_runtime_delegates_engine_to_backend() -> None:
    source = (ROOT / "tools" / "voxcpm2" / "generic_project_runtime.py").read_text(
        encoding="utf-8"
    )

    assert "backend = get_backend(request.get(\"speech_backend\") or DEFAULT_BACKEND_ID)" in source
    assert "backend.process_environment(" in source
    assert "backend.build_renderer_command(" in source
    assert "backend.build_master_command(" in source
    assert "CUDA_VISIBLE_DEVICES" not in source
    assert "HF_HUB_OFFLINE" not in source
    assert "--archive-root" not in source


def test_clean_core_does_not_own_model_specific_command_arguments() -> None:
    source = (ROOT / "tools" / "voxcpm2" / "clean_production_core.py").read_text(
        encoding="utf-8"
    )
    assert "backend.build_renderer_command(" in source
    assert "backend.build_master_command(" in source
    assert "backend.process_environment(" in source
    assert "CUDA_VISIBLE_DEVICES" not in source
    assert "HF_HUB_OFFLINE" not in source
    assert "get_backend(" in source
