#!/usr/bin/env python3
"""Сгенерировать ``docs/env-reference.md`` — полный список env-переменных кода.

Зачем: аудит нашёл 89 переменных, которые код читает, но которые нигде не
описаны. ``.env.example`` — это курируемая инструкция «что стоит трогать», и
раздувать её всеми техническими именами неправильно; но и не иметь полного
перечня нельзя: тогда «какие переменные вообще существуют» выясняется grep'ом
по пяти каталогам.

Документ генерируется из кода, поэтому не расходится с ним:

    python scripts/render_env_reference.py            # переписать
    python scripts/render_env_reference.py --check    # проверить (CI)

Колонки: имя, где читается, литеральный дефолт (если он есть в коде), описан
ли в ``.env.example``, управляется ли стартовой политикой (``POLICY_OWNED``).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUTPUT = ROOT / "docs" / "env-reference.md"
SCAN_DIRS = ("core", "services", "pipelines", "handlers", "converters", "tools", "scripts")
ROOT_FILES = ("main.py", "bot_new.py", "bot.py")

#: Переменные, которыми управляет ОС/интерпретатор/CI, а не проект.
SYSTEM_VARS = {
    "PATH", "HOME", "USERPROFILE", "TEMP", "TMP", "TMPDIR", "APPDATA",
    "LOCALAPPDATA", "PROGRAMFILES", "SYSTEMROOT", "COMSPEC", "SHELL",
    "PYTHONPATH", "PYTHONIOENCODING", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
    "LANG", "LC_ALL", "TZ", "CI", "GITHUB_ACTIONS", "GITHUB_REF",
    "GITHUB_EVENT_NAME", "GITHUB_REPOSITORY", "CUDA_VISIBLE_DEVICES",
    "NO_PROXY", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
}

#: (регулярка, индекс группы с именем, индекс группы с дефолтом или None)
PATTERNS = (
    (re.compile(r"os\.getenv\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']\s*(?:,\s*([\"'][^\"']*[\"']|[-+]?\d+(?:\.\d+)?))?"), 1, 2),
    (re.compile(r"os\.environ\.get\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']\s*(?:,\s*([\"'][^\"']*[\"']|[-+]?\d+(?:\.\d+)?))?"), 1, 2),
    (re.compile(r"os\.environ\.setdefault\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']\s*,\s*([\"'][^\"']*[\"']|[-+]?\d+(?:\.\d+)?)"), 1, 2),
    (re.compile(r"os\.environ\[\s*[\"']([A-Z][A-Z0-9_]*)[\"']\s*\]"), 1, None),
    (re.compile(r"env(?:iron)?\.get\(\s*[\"']([A-Z][A-Z0-9_]*)[\"']"), 1, None),
)


@dataclass
class Entry:
    name: str
    files: set[str] = field(default_factory=set)
    defaults: set[str] = field(default_factory=set)
    written: bool = False


def _sources() -> list[Path]:
    files: list[Path] = []
    for name in ROOT_FILES:
        path = ROOT / name
        if path.is_file():
            files.append(path)
    for directory in SCAN_DIRS:
        base = ROOT / directory
        if base.is_dir():
            files.extend(
                path
                for path in sorted(base.rglob("*.py"))
                if "__pycache__" not in path.parts
            )
    return files


def collect() -> dict[str, Entry]:
    entries: dict[str, Entry] = {}
    for path in _sources():
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8", errors="ignore")
        for pattern, name_group, default_group in PATTERNS:
            # Match across line breaks so Ruff joining or splitting a call cannot
            # add or remove an environment variable from the generated contract.
            for match in pattern.finditer(source):
                line_start = source.rfind("\n", 0, match.start()) + 1
                if source[line_start:match.start()].lstrip().startswith("#"):
                    continue
                name = match.group(name_group)
                if name in SYSTEM_VARS or name.startswith(("GITHUB_", "RUNNER_")):
                    continue
                entry = entries.setdefault(name, Entry(name=name))
                entry.files.add(relative)
                if default_group:
                    value = match.group(default_group)
                    if value:
                        entry.defaults.add(value.strip("\"'"))
                matched_source = source[line_start:match.end()]
                if "os.environ[" in matched_source or "setdefault" in matched_source:
                    entry.written = True
    return entries


def _documented_names() -> set[str]:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    return {
        match.group(1)
        for match in re.finditer(r"(?m)^[#\s]*([A-Z][A-Z0-9_]*)=", text)
    }


def _policy_owned() -> dict[str, list[str]]:
    """Реестр POLICY_OWNED из живого кода, в отдельном процессе.

    Политики при применении пишут в ``os.environ``; выполнять их в процессе
    генератора нельзя, поэтому берём снимок из дочернего Python. Так таблица
    показывает реальные владельцы, а не список, переписанный руками.
    """
    code = (
        "import json;"
        "from services.gemini_max_quality import configure_max_quality_env;"
        "from services.livedub_quality_runtime import configure_gemini_policy;"
        "from services.env_policy import POLICY_OWNED;"
        "configure_max_quality_env();"
        "configure_gemini_policy();"
        "print(json.dumps({k: list(v) for k, v in POLICY_OWNED.items()}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT), capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        print(
            "env reference: не удалось получить POLICY_OWNED: "
            f"{result.stderr.strip()[:400]}",
            file=sys.stderr,
        )
        return {}
    return json.loads(result.stdout)


def render() -> str:
    entries = collect()
    documented = _documented_names()
    policy = _policy_owned()

    lines = [
        "# Справочник переменных окружения",
        "",
        "Сгенерировано `scripts/render_env_reference.py` из кода — не редактируйте вручную.",
        "Курируемая инструкция «что стоит трогать» — `.env.example`; здесь полный перечень,",
        "включая внутренние и диагностические переменные.",
        "",
        f"Всего переменных: **{len(entries)}**; описано в `.env.example`: "
        f"**{len(set(entries) & documented)}**; не описано: "
        f"**{len(set(entries) - documented)}**; управляется стартовой политикой: "
        f"**{len(set(entries) & set(policy))}**.",
        "",
        "| Переменная | Где читается | Дефолт в коде | В `.env.example` | Политика |",
        "|---|---|---|---|---|",
    ]
    for name in sorted(entries):
        entry = entries[name]
        files = sorted(entry.files)
        where = ", ".join(f"`{path}`" for path in files[:3])
        if len(files) > 3:
            where += f" и ещё {len(files) - 3}"
        default = ", ".join(f"`{value}`" for value in sorted(entry.defaults)) or "—"
        lines.append(
            "| `{name}` | {where} | {default} | {doc} | {policy} |".format(
                name=name,
                where=where,
                default=default,
                doc="да" if name in documented else "—",
                policy="да" if name in policy else "—",
            )
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    rendered = render()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != rendered:
            print("env reference: устарел, выполните python scripts/render_env_reference.py")
            return 1
        print("env reference: актуален")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"env reference: обновлён {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
