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
    return subprocess.run(
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
