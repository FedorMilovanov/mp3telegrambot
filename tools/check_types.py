#!/usr/bin/env python3
"""Статическая проверка типов для типизированного среза репозитория.

Зачем отдельный инструмент, а не строка ``mypy`` в CI: у проверки типов в
этом репозитории есть **срез**. ``mypy .`` даёт ~700 ошибок в легаси-коде
``services/`` и ``pipelines/``; включить его целиком — значит получить гейт,
который всегда красный, и через неделю его отключат. Поэтому проверяется
ровно то, что уже приведено к нулю, и срез растёт по одному пакету за PR.

Жёсткость настройки живёт в ``[tool.mypy]`` (``pyproject.toml``), а не здесь:
один источник правды. Этот модуль отвечает только за то, *что* проверять, и
за понятную диагностику, если mypy не установлен.

    python tools/check_types.py            # проверка (CI, verify_repo)
    python tools/check_types.py --verbose  # печатать команду и полный вывод
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Что уже проверяется строго. Расширяется по одному пакету за PR, только
#: когда ошибок в нём ноль: «зелёный срез» — контракт, «почти зелёный» — нет.
#: Тест ``tests/test_typecheck_gate.py`` сверяет этот список с документацией.
TYPED_SLICES: tuple[str, ...] = ("core",)

#: mypy возвращает 2 на фатальных ошибках (нет модуля, битый конфиг).
EXIT_USAGE = 2


def build_command(slices: tuple[str, ...] = TYPED_SLICES, extra: tuple[str, ...] = ()) -> list[str]:
    """Команда проверки. Конфиг подхватывается из ``pyproject.toml`` по cwd."""
    return [sys.executable, "-m", "mypy", *slices, *extra]


def check(
    slices: tuple[str, ...] = TYPED_SLICES,
    extra: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        build_command(slices, extra),
        cwd=str(ROOT),
        capture_output=True,
        text=True,
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
        "slices",
        nargs="*",
        default=list(TYPED_SLICES),
        help="каталоги/пакеты для проверки (по умолчанию — объявленный срез)",
    )
    parser.add_argument("--verbose", action="store_true", help="печатать команду и полный вывод mypy")
    args = parser.parse_args(argv)

    slices = tuple(args.slices)
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
    if result.returncode != 0:
        if output:
            print(output)
        print(f"TYPECHECK_FAILED: срез {slices} содержит {error_count(output)} ошибок типов", file=sys.stderr)
        return 1

    print(f"TYPECHECK_OK: срез {slices} — 0 ошибок типов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
