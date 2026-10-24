#!/usr/bin/env python3
"""Сгенерировать ``docs/README.md`` — навигацию по всей документации проекта.

Зачем генератор, а не рукописный индекс: в ``docs/`` 73 markdown-файла, включая
исторические отчёты валидации. Рукописный список расходится с каталогом через
две недели, и именно так появляются «документы, о которых никто не знает».
Здесь список строится из файловой системы, а ``--check`` не даёт ему устареть.

    python tools/render_docs_index.py            # переписать docs/README.md
    python tools/render_docs_index.py --check    # проверить (CI, pre-push)

Заголовок берётся из первой строки-заголовка файла, описание — из первой
непустой строки после него. Дубли ``*_latest.md`` запрещены отдельной проверкой
(``--check`` падает): файл, который называется «последний», устаревает молча.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUTPUT = DOCS / "README.md"

#: Префикс, по которому раскладываем файлы по разделам. Порядок важен:
#: первое совпадение выигрывает, поэтому «dub_» идёт раньше общего «audit».
SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Эксплуатация и контракты",
        (
            "dependency-policy",
            "env-reference",
            "typechecking",
            "model-catalog",
            "gemini-operations",
            "runtime-reliability",
            "local-bot-api-repair",
            "generated_pages_archive",
            "youtube-",
            "tts-weighted-runner",
            "AUDIT_2026-10-23_NEXT",
        ),
    ),
    (
        "Dub Studio",
        ("dub_",),
    ),
    (
        "VoxCPM2 и TTS",
        ("voxcpm2", "shorts_factory_max"),
    ),
    (
        "Перевод и редактура",
        ("translation_editorial",),
    ),
    (
        "Аудиты и отчёты о качестве",
        ("AUDIT_", "quality_audit", "prompt-intelligence", "telegraph_repair", "unified_mode_menu", "V3_PROGRESS"),
    ),
)

FALLBACK_SECTION = "Прочее"


@dataclass(frozen=True)
class DocEntry:
    path: str  # относительный путь от корня репозитория, posix
    title: str
    summary: str
    section: str

    @property
    def link(self) -> str:
        return self.path.removeprefix("docs/")


#: Подкаталоги `docs/` имеют собственную область, поэтому проверяются раньше
#: префиксов имени: иначе `quality_audits/youtube-…md` попадал в эксплуатацию
#: только потому, что имя начинается с «youtube-».
SUBDIR_SECTIONS = {
    "quality_audits": "Аудиты и отчёты о качестве",
    "quality_audit_reports": "Аудиты и отчёты о качестве",
    "voxcpm2": "VoxCPM2 и TTS",
}


def _section_for(relative: str) -> str:
    parts = Path(relative).parts
    if len(parts) > 2 and parts[1] in SUBDIR_SECTIONS:
        return SUBDIR_SECTIONS[parts[1]]
    name = parts[-1]
    for section, prefixes in SECTIONS:
        if name.startswith(prefixes):
            return section
    return FALLBACK_SECTION


def _clean(line: str) -> str:
    # Ссылки оставляем текстом, а inline-код и подчёркивания в именах файлов —
    # как есть: без них `render_env_reference.py` превращался в
    # `renderenvreference.py`.
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
    return text.replace("**", "").replace("__", "").strip()


#: Строки, которые ничего не говорят о содержании файла: статусы отчётов,
#: элементы списков, таблицы, цитаты, блоки кода.
_NOISE_PREFIXES = ("-", "*", "|", ">", "#", "```", "Результат", "Result", "Status", "Статус")


def _title_and_summary(text: str, fallback: str) -> tuple[str, str]:
    """Заголовок и первая содержательная строка.

    Отчёты валидации начинаются с «- Result: PASS», и такой «обзор» в индексе
    бесполезен: он не отличает один отчёт от другого. Поэтому ищем первую
    строку-прозу длиной от 40 символов, а статусные строки пропускаем.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    title = ""
    title_index = -1
    for index, line in enumerate(lines):
        if line.startswith("# "):
            title, title_index = line[2:].strip(), index
            break
    if not title:
        for index, line in enumerate(lines):
            if line.startswith("#"):
                title, title_index = line.lstrip("#").strip(), index
                break

    candidates: list[str] = []
    for line in lines[title_index + 1 :] if title_index >= 0 else lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(_NOISE_PREFIXES):
            continue
        cleaned = _clean(stripped)
        if cleaned:
            candidates.append(cleaned)
    summary = next((item for item in candidates if len(item) >= 40), "")
    if not summary and candidates:
        summary = candidates[0]
    if len(summary) > 160:
        cut = summary[:160].rsplit(" ", 1)[0]
        summary = f"{cut}…"
    return (title or fallback, summary)


def _display(path: Path) -> str:
    """Путь для сообщений и индекса: относительно корня, если он внутри него.

    Функции вызываются и на временных каталогах (негативные тесты), поэтому
    ``relative_to(ROOT)`` без защиты падал бы на ``/tmp/...``.
    """
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def collect(docs: Path = DOCS) -> list[DocEntry]:
    entries: list[DocEntry] = []
    for path in sorted(docs.rglob("*.md")):
        if path == OUTPUT or path.name == OUTPUT.name and path.parent == docs:
            continue
        relative = _display(path)
        title, summary = _title_and_summary(
            path.read_text(encoding="utf-8", errors="replace"), fallback=path.stem
        )
        entries.append(DocEntry(relative, title, summary, _section_for(relative)))
    return entries


PREAMBLE = """# Документация MP3Bot

Этот файл **сгенерирован**: `python tools/render_docs_index.py`.
Руками не правится — `--check` в CI и в `pre-push` падает, если список
разошёлся с каталогом `docs/`.

С чего начинать:

- **что проект умеет и как его запустить** — [`../README.md`](../README.md);
- **устройство и правила слоёв** — [`../ARCHITECTURE.md`](../ARCHITECTURE.md);
- **как сообщить об уязвимости и что считается секретом** — [`../SECURITY.md`](../SECURITY.md);
- **как менять код и какие гейты гонять локально** — [`../CONTRIBUTING.md`](../CONTRIBUTING.md);
- **какие переменные окружения существуют** — [`env-reference.md`](env-reference.md) (тоже генерируется);
- **что делать дальше по качеству** — [`AUDIT_2026-10-23_NEXT.md`](AUDIT_2026-10-23_NEXT.md).

Правила именования: имя файла описывает **область и дату**, а не «новизну».
Суффикс `_latest` запрещён: файл с таким именем устаревает молча, и читатель
не может понять, что перед ним старое.
"""


def render(entries: list[DocEntry]) -> str:
    buckets: dict[str, list[DocEntry]] = {}
    for entry in entries:
        buckets.setdefault(entry.section, []).append(entry)

    order = [section for section, _ in SECTIONS] + [FALLBACK_SECTION]
    out = [PREAMBLE, f"Всего файлов: **{len(entries)}**.\n"]
    for section in order:
        bucket = buckets.get(section)
        if not bucket:
            continue
        out.append(f"## {section}\n")
        out.append("| Файл | О чём |")
        out.append("|---|---|")
        for entry in sorted(bucket, key=lambda item: item.path.lower()):
            summary = entry.summary.replace("|", "\\|")[:200] or "—"
            out.append(f"| [`{entry.link}`]({entry.link}) | {summary} |")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def check(entries: list[DocEntry], output: Path = OUTPUT) -> list[str]:
    problems: list[str] = []
    for entry in entries:
        if Path(entry.path).name.endswith("_latest.md"):
            problems.append(
                f"{entry.path}: имя оканчивается на _latest.md — переименуйте по области и дате"
            )
    expected = render(entries)
    if not output.is_file():
        problems.append(f"{_display(output)} отсутствует — запустите без --check")
    elif output.read_text(encoding="utf-8") != expected:
        problems.append(
            f"{_display(output)} устарел — запустите `python tools/render_docs_index.py`"
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="только проверить, не переписывая")
    args = parser.parse_args(argv)

    entries = collect()
    if args.check:
        problems = check(entries)
        for problem in problems:
            print(f"[FAIL] {problem}")
        if not problems:
            print(f"docs index: {len(entries)} файлов, актуален")
            return 0
        return 1

    OUTPUT.write_text(render(entries), encoding="utf-8")
    print(f"docs index: записано {len(entries)} файлов в {_display(OUTPUT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
