#!/usr/bin/env python3
"""Установить подмножество ``requirements-lock.txt``, не выдумывая версии.

Зачем: ``.github/workflows/windows-bootstrap-ci.yml`` ставил нужные для
контрактных тестов пакеты руками — ``packaging==26.2``, ``pytest==8.4.2``,
``yt-dlp==2026.7.4``. Это второй владелец версий: lock уже фиксирует
``pytest 9.1.1`` и ``yt-dlp 2026.10.21``, то есть Windows-прогон проверял
контракты на другом наборе пакетов, чем production и основной CI. Любой
Dependabot-бамп lock'а оставлял этот workflow на старой версии, и никто бы
этого не заметил.

Теперь версия берётся только из lock:

    python tools/install_from_lock.py packaging pytest yt-dlp yt-dlp-ejs

Пакеты, которых нет в lock, — ошибка (а не «поставим что-нибудь»): это значит,
что имя опечатано или зависимость не задекларирована.

Usage:
    python tools/install_from_lock.py [--dry-run] [--lock PATH] NAME [NAME ...]
"""

from __future__ import annotations

import argparse
import subprocess as subprocess
import sys as sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "requirements-lock.txt"


def locked_versions(lock_path: Path = DEFAULT_LOCK) -> dict[str, str]:
    """Каноническое имя (нижний регистр, ``-``/``_``/``.`` -> ``-``) -> версия."""
    pinned: dict[str, str] = {}
    for raw in Path(lock_path).read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, version = line.partition("==")
        if not separator:
            raise SystemExit(f"lock entry is not an exact pin: {line}")
        # extras в lock не встречаются, но срезать их безопасно
        name = name.split("[", 1)[0].strip()
        canonical = name.lower().replace("_", "-").replace(".", "-")
        pinned[canonical] = version.strip()
    return pinned


def canonical(name: str) -> str:
    return str(name).strip().lower().replace("_", "-").replace(".", "-")


def select(names: list[str], lock_path: Path = DEFAULT_LOCK) -> list[str]:
    """Вернуть ``name==version`` для каждого имени; неизвестное имя — ошибка."""
    pinned = locked_versions(lock_path)
    unknown = [name for name in names if canonical(name) not in pinned]
    if unknown:
        raise SystemExit(
            "these packages are not pinned in "
            f"{Path(lock_path).name}: {', '.join(unknown)}. "
            "Declare them in requirements.txt and regenerate the lock."
        )
    return [f"{canonical(name)}=={pinned[canonical(name)]}" for name in names]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("packages", nargs="+", metavar="NAME")
    parser.add_argument("--lock", default=str(DEFAULT_LOCK))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the pinned requirement lines instead of installing",
    )
    args = parser.parse_args(argv)

    requirements = select(args.packages, Path(args.lock))
    if args.dry_run:
        print("\n".join(requirements))
        return 0

    command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    command.extend(requirements)
    print("installing from lock:", " ".join(requirements), flush=True)
    return subprocess.call(command)  # noqa: S603 -- current interpreter installs exact locked pins


if __name__ == "__main__":
    raise SystemExit(main())
