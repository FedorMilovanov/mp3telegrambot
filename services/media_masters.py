"""Replaceable media-master and final-validation boundaries for Dub production."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

MEDIA_MASTER_POLICY = "backend-neutral-media-master-v1"
FINAL_MEDIA_VALIDATOR_POLICY = "backend-neutral-final-media-validator-v1"


@dataclass(frozen=True)
class MediaMasterRuntime:
    master_id: str
    python_executable: Path
    entrypoint: Path
    import_module: str

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["python_executable"] = str(self.python_executable)
        payload["entrypoint"] = str(self.entrypoint)
        payload["media_master_policy"] = MEDIA_MASTER_POLICY
        return payload


@dataclass(frozen=True)
class MediaMasterRequest:
    source_video: Path
    russian_wav: Path
    work_dir: Path
    mixed_video: Path
    russian_only_video: Path
    original_level: float
    target_i: float = -14.0
    target_lra: float = 9.0
    target_tp: float = -1.0

    def __post_init__(self) -> None:
        for name in (
            "source_video",
            "russian_wav",
            "work_dir",
            "mixed_video",
            "russian_only_video",
        ):
            object.__setattr__(self, name, Path(getattr(self, name)))
        level = float(self.original_level)
        if not 0.0 <= level <= 1.0:
            raise ValueError("original_level должен быть в диапазоне 0..1.")
        object.__setattr__(self, "original_level", level)


@dataclass(frozen=True)
class FinalMediaValidation:
    validator_id: str
    mixed_video: str
    russian_only_video: str
    passed: bool
    details: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "validator_id": self.validator_id,
            "mixed_video": self.mixed_video,
            "russian_only_video": self.russian_only_video,
            "passed": self.passed,
            "details": dict(self.details),
            "final_media_validator_policy": FINAL_MEDIA_VALIDATOR_POLICY,
        }


@runtime_checkable
class MediaMaster(Protocol):
    master_id: str

    def runtime_paths(
        self,
        repo_root: Path,
        request: Mapping[str, Any],
        *,
        fallback_python: Path | None = None,
    ) -> MediaMasterRuntime: ...

    def build_command(
        self,
        runtime: MediaMasterRuntime,
        request: MediaMasterRequest,
    ) -> list[str]: ...


@runtime_checkable
class FinalMediaValidator(Protocol):
    validator_id: str

    def validate(
        self,
        *,
        mixed_video: Path,
        russian_only_video: Path,
    ) -> FinalMediaValidation: ...


class ConstantMixMediaMaster:
    master_id = "constant-mix"

    def runtime_paths(
        self,
        repo_root: Path,
        request: Mapping[str, Any],
        *,
        fallback_python: Path | None = None,
    ) -> MediaMasterRuntime:
        repo = Path(repo_root).resolve()
        mode = str(request.get("translation_mode") or "").casefold().strip()
        if mode == "direct":
            entrypoint = repo / "tools" / "voxcpm2" / "master_monolithic_mix.py"
            module = "tools.voxcpm2.master_monolithic_mix"
        else:
            entrypoint = (
                repo
                / "tools"
                / "voxcpm2"
                / "examples"
                / "john_piper_z20py4yqhyq"
                / "master_constant_mix.py"
            )
            module = (
                "tools.voxcpm2.examples.john_piper_z20py4yqhyq."
                "master_constant_mix"
            )
        explicit = str(request.get("media_python") or "").strip()
        python = Path(explicit).expanduser().resolve() if explicit else Path(
            fallback_python or sys.executable
        ).resolve()
        return MediaMasterRuntime(
            master_id=self.master_id,
            python_executable=python,
            entrypoint=entrypoint,
            import_module=module,
        )

    def build_command(
        self,
        runtime: MediaMasterRuntime,
        request: MediaMasterRequest,
    ) -> list[str]:
        return [
            str(runtime.python_executable),
            str(runtime.entrypoint),
            "--source-video",
            str(request.source_video),
            "--russian-wav",
            str(request.russian_wav),
            "--work-dir",
            str(request.work_dir),
            "--mixed-video",
            str(request.mixed_video),
            "--russian-only-video",
            str(request.russian_only_video),
            "--original-level",
            f"{request.original_level:.6f}",
            "--target-i",
            f"{request.target_i:.3f}",
            "--target-lra",
            f"{request.target_lra:.3f}",
            "--target-tp",
            f"{request.target_tp:.3f}",
        ]


class FFprobeFinalMediaValidator:
    validator_id = "ffprobe-av-contract"

    @staticmethod
    def _probe(path: Path) -> dict[str, Any]:
        executable = shutil.which("ffprobe")
        if not executable:
            raise RuntimeError("ffprobe не найден в PATH.")
        if not path.is_file() or path.stat().st_size <= 1024:
            raise RuntimeError(f"Финальный файл отсутствует или слишком мал: {path}")
        proc = subprocess.run(
            [
                executable,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "json",
                str(path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            ),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffprobe отклонил {path.name}: {(proc.stderr or '')[-1200:]}"
            )
        payload = json.loads(proc.stdout or "{}")
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
        streams = {
            str(item.get("codec_type") or "")
            for item in payload.get("streams") or []
        }
        if duration <= 0 or not {"audio", "video"}.issubset(streams):
            raise RuntimeError(
                f"Некорректный AV-контракт {path.name}: duration={duration}, streams={sorted(streams)}"
            )
        return {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "duration": duration,
            "streams": sorted(streams),
        }

    def validate(
        self,
        *,
        mixed_video: Path,
        russian_only_video: Path,
    ) -> FinalMediaValidation:
        details = {
            "mixed": self._probe(Path(mixed_video)),
            "russian_only": self._probe(Path(russian_only_video)),
        }
        return FinalMediaValidation(
            validator_id=self.validator_id,
            mixed_video=str(Path(mixed_video)),
            russian_only_video=str(Path(russian_only_video)),
            passed=True,
            details=details,
        )


_MASTERS: dict[str, MediaMaster] = {}
_VALIDATORS: dict[str, FinalMediaValidator] = {}


def register_media_master(master: MediaMaster) -> None:
    master_id = str(getattr(master, "master_id", "")).casefold().strip()
    if not master_id:
        raise ValueError("Media master требует master_id.")
    existing = _MASTERS.get(master_id)
    if existing is not None and existing is not master:
        raise RuntimeError(f"Media master уже зарегистрирован: {master_id}")
    _MASTERS[master_id] = master


def get_media_master(value: object = "constant-mix") -> MediaMaster:
    master_id = str(value or "constant-mix").casefold().strip()
    try:
        return _MASTERS[master_id]
    except KeyError as exc:
        raise RuntimeError(
            f"Неизвестный media master {value!r}; доступно: {', '.join(sorted(_MASTERS)) or '—'}"
        ) from exc


def register_final_validator(validator: FinalMediaValidator) -> None:
    validator_id = str(getattr(validator, "validator_id", "")).casefold().strip()
    if not validator_id:
        raise ValueError("Final media validator требует validator_id.")
    existing = _VALIDATORS.get(validator_id)
    if existing is not None and existing is not validator:
        raise RuntimeError(f"Final media validator уже зарегистрирован: {validator_id}")
    _VALIDATORS[validator_id] = validator


def get_final_validator(value: object = "ffprobe-av-contract") -> FinalMediaValidator:
    validator_id = str(value or "ffprobe-av-contract").casefold().strip()
    try:
        return _VALIDATORS[validator_id]
    except KeyError as exc:
        raise RuntimeError(
            "Неизвестный final media validator "
            f"{value!r}; доступно: {', '.join(sorted(_VALIDATORS)) or '—'}"
        ) from exc


register_media_master(ConstantMixMediaMaster())
register_final_validator(FFprobeFinalMediaValidator())


__all__ = [
    "FINAL_MEDIA_VALIDATOR_POLICY",
    "MEDIA_MASTER_POLICY",
    "ConstantMixMediaMaster",
    "FFprobeFinalMediaValidator",
    "FinalMediaValidation",
    "FinalMediaValidator",
    "MediaMaster",
    "MediaMasterRequest",
    "MediaMasterRuntime",
    "get_final_validator",
    "get_media_master",
    "register_final_validator",
    "register_media_master",
]
