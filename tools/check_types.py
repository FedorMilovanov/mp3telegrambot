#!/usr/bin/env python3
"""Статическая проверка типов для типизированного среза репозитория.

Зачем отдельный инструмент, а не строка ``mypy`` в CI: у проверки типов в
этом репозитории есть **срез**. Полный ``mypy --strict .`` даёт тысячи ошибок
в легаси ``services/`` и в тестах; включить его целиком — значит получить
гейт, который всегда красный, и через неделю его отключат. Поэтому
проверяется ровно то, что уже приведено к нулю, и срез растёт только вперёд.

Жёсткость настройки живёт в ``[tool.mypy]`` (``pyproject.toml``), а не здесь:
один источник правды. Этот модуль отвечает только за то, *что* проверять, и
за понятную диагностику, если mypy не установлен.

    python tools/check_types.py            # проверка (CI, verify_repo)
    python tools/check_types.py --verbose  # печатать команду и полный вывод
    python tools/check_types.py core       # произвольный каталог/файл
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Slice:
    """Что проверяется и что из этого осознанно отложено.

    ``exclude`` — пути внутри среза, которые ещё не приведены к нулю. Они
    остаются видимыми здесь, а не тонут в молчаливом ``follow_imports``:
    исключение требует причины и снимается тем PR, который закончит работу.
    """

    path: str
    exclude: tuple[str, ...] = field(default=())


#: Единственный список того, что проверяется строго. Расширяется по одному
#: пути за PR и только когда ошибок в нём ноль: «зелёный срез» — контракт,
#: «почти зелёный» — нет. Тест ``tests/test_typecheck_gate.py`` сверяет этот
#: список с таблицей в ``docs/typechecking.md``.
TYPED_SLICES: tuple[Slice, ...] = (
    Slice("core"),
    Slice(
        "pipelines",
        exclude=(
            # P0-3: файл режется на этапы (анализ → рендер → публикация) в
            # ближайших PR. Типизировать 3 000 строк, которые переезжают в
            # модули, значит дважды переписывать одни и те же сигнатуры.
            "pipelines/main_pipeline.py",
        ),
    ),
    Slice("converters/__init__.py"),
    Slice("converters/caption.py"),
    Slice("handlers/__init__.py"),
    Slice("handlers/dub_delivery.py"),
    Slice("handlers/dub_health.py"),
    Slice("handlers/dub_multicommand.py"),
    Slice("handlers/dub_quickstart.py"),
    Slice("handlers/mode_command.py"),
    Slice("handlers/dub_studio_registration.py"),
    Slice("services/__init__.py"),
    Slice("services/async_process.py"),
    Slice("services/async_worker.py"),
    Slice("services/audio_cache_maintenance.py"),
    Slice("services/bgutil_http_runtime.py"),
    Slice("services/bgutil_orphan_recovery.py"),
    Slice("services/bot_lifecycle.py"),
    Slice("services/clip_renderer.py"),
    Slice("services/conspect_audit_runtime.py"),
    Slice("services/conspect_quality_contract.py"),
    Slice("services/database_migrations.py"),
    Slice("services/dub_delivery.py"),
    Slice("services/dub_execution.py"),
    Slice("services/dub_preflight.py"),
    Slice("services/dub_reference_strategies.py"),
    Slice("services/dub_release_health_v64.py"),
    Slice("services/dub_rendering.py"),
    Slice("services/dub_studio.py"),
    Slice("services/dub_studio_runtime.py"),
    Slice("services/dub_worker.py"),
    Slice("services/dub_worker_release.py"),
    Slice("services/env_policy.py"),
    Slice("services/ffmpeg.py"),
    Slice("services/gemini_capacity_control.py"),
    Slice("services/gemini_error_policy.py"),
    Slice("services/gemini_max_quality.py"),
    Slice("services/gemini_model_status.py"),
    Slice("services/gemini_qa_policy.py"),
    Slice("services/gemini_quota_domains.py"),
    Slice("services/gemini_router.py"),
    Slice("services/health_report.py"),
    Slice("services/highlights_candidate_gate.py"),
    Slice("services/latency_trace.py"),
    Slice("services/livedub_audio_cache_recovery.py"),
    Slice("services/livedub_audio_companion.py"),
    Slice("services/livedub_audio_quality_guard.py"),
    Slice("services/livedub_delivery_coordinator.py"),
    Slice("services/livedub_info_evidence.py"),
    Slice("services/livedub_output_policy.py"),
    Slice("services/livedub_publication.py"),
    Slice("services/livedub_publication_core.py"),
    Slice("services/livedub_qa_trust.py"),
    Slice("services/livedub_quality_runtime.py"),
    Slice("services/livedub_ru_provenance.py"),
    Slice("services/local_botapi_bootstrap.py"),
    Slice("services/local_botapi_diagnostics.py"),
    Slice("services/local_botapi_required.py"),
    Slice("services/local_botapi_runtime.py"),
    Slice("services/media_delivery_probe.py"),
    Slice("services/media_masters.py"),
    Slice("services/mp3_conversion.py"),
    Slice("services/operator_runtime_status.py"),
    Slice("services/polling_reliability_runtime.py"),
    Slice("services/pre_main_policy.py"),
    Slice("services/process_executables.py"),
    Slice("services/process_singleton.py"),
    Slice("services/quiz_sessions.py"),
    Slice("services/restart_state_runtime.py"),
    Slice("services/runtime_build_identity.py"),
    Slice("services/runtime_dependency_lock.py"),
    Slice("services/runtime_manifest.py"),
    Slice("services/segment_render.py"),
    Slice("services/shorts_duration_safety.py"),
    Slice("services/shorts_factory_disk_guard.py"),
    Slice("services/shorts_factory_execution_guard.py"),
    Slice("services/shorts_factory_full_video.py"),
    Slice("services/shorts_factory_long_fit.py"),
    Slice("services/shorts_factory_media.py"),
    Slice("services/shorts_factory_publication.py"),
    Slice("services/shorts_factory_quality_gate.py"),
    Slice("services/shorts_factory_retry_cache.py"),
    Slice("services/shorts_factory_source.py"),
    Slice("services/shorts_factory_video_quality.py"),
    Slice("services/shorts_static_policy.py"),
    Slice("services/shorts_subtitle_burn.py"),
    Slice("services/shorts_subtitle_integrity.py"),
    Slice("services/shorts_transcription.py"),
    Slice("services/speech_backends"),
    Slice("services/study_synthesis_policy.py"),
    Slice("services/study_synthesis_runtime.py"),
    Slice("services/telegraph_edit.py"),
    Slice("services/telegraph_repair.py"),
    Slice("services/translation_editorial.py"),
    Slice("services/translation_editorial_factory.py"),
    Slice("services/translation_editorial_pack_contract.py"),
    Slice("services/translation_editorial_pack_v2.py"),
    Slice("services/tts_profile_selection.py"),
    Slice("services/tts_weighted_smoke.py"),
    Slice("services/tts_weighted_smoke_attestation.py"),
    Slice("services/tts_weighted_smoke_runner.py"),
    Slice("services/youtube_po_token_runtime.py"),
    Slice("services/youtube_transcript.py"),
)

#: mypy возвращает 2 на фатальных ошибках (нет модуля, битый конфиг).
EXIT_USAGE = 2


def _exclude_args(slices: tuple[Slice, ...]) -> list[str]:
    args: list[str] = []
    for slice_ in slices:
        for path in slice_.exclude:
            # Якоря обязательны: без них `pipelines/main_pipeline.py` как regex
            # совпадёт и с `pipelines/main_pipeline_extra.py`.
            args.append(f"--exclude=^{re.escape(path)}$")
    return args


def build_command(
    slices: tuple[Slice, ...] | tuple[str, ...] = TYPED_SLICES,
    extra: tuple[str, ...] = (),
) -> list[str]:
    """Команда проверки. Конфиг подхватывается из ``pyproject.toml`` по cwd."""
    resolved = tuple(s if isinstance(s, Slice) else Slice(s) for s in slices)
    paths = [slice_.path for slice_ in resolved]
    return [sys.executable, "-m", "mypy", *paths, *_exclude_args(resolved), *extra]


def check(
    slices: tuple[Slice, ...] | tuple[str, ...] = TYPED_SLICES,
    extra: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- current interpreter and declared mypy slices
        build_command(slices, extra),
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def missing_mypy(result: subprocess.CompletedProcess[str]) -> bool:
    """mypy не установлен — это сломанное окружение, а не ошибки типов.

    Различать важно: иначе сообщение «No module named mypy» будет выглядеть
    как провал типизации и человек пойдёт искать несуществующие ошибки в коде.
    """
    return "No module named mypy" in f"{result.stdout}\n{result.stderr}"


def error_count(output: str) -> int:
    return len(re.findall(r"^\S+:\d+: error:", output, flags=re.MULTILINE))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "paths",
        nargs="*",
        help="каталоги/файлы для проверки (по умолчанию — объявленный срез)",
    )
    parser.add_argument("--verbose", action="store_true", help="печатать команду и полный вывод mypy")
    args = parser.parse_args(argv)

    slices: tuple[Slice, ...] = tuple(Slice(p) for p in args.paths) or TYPED_SLICES
    result = check(slices)

    if missing_mypy(result):
        print(
            "TYPECHECK_FAILED: mypy не установлен. "
            "Установите dev-зависимости: python -m pip install -r requirements-dev.txt",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if args.verbose:
        print("$ " + " ".join(build_command(slices)))
    output = (result.stdout + result.stderr).strip()
    paths = tuple(slice_.path for slice_ in slices)
    if result.returncode != 0:
        if output:
            print(output)
        print(f"TYPECHECK_FAILED: срез {paths} содержит {error_count(output)} ошибок типов", file=sys.stderr)
        return 1

    print(f"TYPECHECK_OK: срез {paths} — 0 ошибок типов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
