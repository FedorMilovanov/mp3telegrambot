from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one hardening anchor, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


def patch_revision_storage() -> None:
    path = ROOT / "core" / "dub_projects.py"
    replace_once(
        path,
        '        editorial = project_dir(project_id) / "editorial"\n'
        '        translation_path = editorial / "translation_ru_approved.txt"\n'
        '        units_path = editorial / "translation_units.json"\n'
        '        _atomic_write_text(translation_path, normalized + "\\n")\n'
        '        _atomic_write_json(units_path, units)\n',
        '        editorial = project_dir(project_id) / "editorial"\n'
        '        revisions = editorial / "revisions"\n'
        '        translation_path = revisions / f"translation_ru_r{revision:03d}.txt"\n'
        '        units_path = revisions / f"translation_units_r{revision:03d}.json"\n'
        '        current_translation = editorial / "translation_ru_approved.txt"\n'
        '        current_units = editorial / "translation_units.json"\n'
        '        _atomic_write_text(translation_path, normalized + "\\n")\n'
        '        _atomic_write_json(units_path, units)\n'
        '        _atomic_write_text(current_translation, normalized + "\\n")\n'
        '        _atomic_write_json(current_units, units)\n',
    )
    replace_once(
        path,
        '            "display_text_path": str(translation_path),\n'
        '            "units_path": str(units_path),\n',
        '            "display_text_path": str(translation_path),\n'
        '            "units_path": str(units_path),\n'
        '            "current_display_text_path": str(current_translation),\n'
        '            "current_units_path": str(current_units),\n',
    )


def patch_docx_order() -> None:
    path = ROOT / "handlers" / "dub_production.py"
    replace_once(
        path,
        '        doc = Document(str(target))\n'
        '        blocks: list[str] = [paragraph.text.strip() for paragraph in doc.paragraphs if paragraph.text.strip()]\n'
        '        for table in doc.tables:\n'
        '            for row in table.rows:\n'
        '                values = [cell.text.strip() for cell in row.cells if cell.text.strip()]\n'
        '                if values:\n'
        '                    blocks.append(" | ".join(values))\n'
        '        return "\\n\\n".join(blocks)\n',
        '        from docx.table import Table\n'
        '        from docx.text.paragraph import Paragraph\n'
        '        from docx.oxml.table import CT_Tbl\n'
        '        from docx.oxml.text.paragraph import CT_P\n'
        '        doc = Document(str(target))\n'
        '        blocks: list[str] = []\n'
        '        for child in doc.element.body.iterchildren():\n'
        '            if isinstance(child, CT_P):\n'
        '                value = Paragraph(child, doc).text.strip()\n'
        '                if value:\n'
        '                    blocks.append(value)\n'
        '            elif isinstance(child, CT_Tbl):\n'
        '                table = Table(child, doc)\n'
        '                for row in table.rows:\n'
        '                    values = [cell.text.strip() for cell in row.cells if cell.text.strip()]\n'
        '                    if values:\n'
        '                        blocks.append(" | ".join(values))\n'
        '        return "\\n\\n".join(blocks)\n',
    )


def patch_preflight_validation() -> None:
    path = ROOT / "pipelines" / "dubbing" / "preflight.py"
    replace_once(
        path,
        '            if not isinstance(units, list) or not units:\n'
        '                blocking.append("В переводе не найдено ни одной редакционной единицы.")\n'
        '            elif any(not str(item.get("display_text") or "").strip() for item in units if isinstance(item, dict)):\n'
        '                blocking.append("Одна из редакционных единиц перевода пуста.")\n',
        '            if not isinstance(units, list) or not units:\n'
        '                blocking.append("В переводе не найдено ни одной редакционной единицы.")\n'
        '            elif any(not isinstance(item, dict) for item in units):\n'
        '                blocking.append("Файл редакционных единиц содержит запись неверного типа.")\n'
        '            elif any(not str(item.get("display_text") or "").strip() for item in units):\n'
        '                blocking.append("Одна из редакционных единиц перевода пуста.")\n',
    )


def patch_privileged_menu() -> None:
    path = ROOT / "main.py"
    replace_once(
        path,
        '        for admin_id in ADMIN_IDS:\n',
        '        for admin_id in sorted(set(ADMIN_IDS) | set(WHITELIST_IDS)):\n',
    )


def patch_help_literal() -> None:
    path = ROOT / "handlers" / "commands.py"
    replace_once(
        path,
        '        f"/dub &lt;URL&gt; — 🎬 VoxCPM2-дубляж из готового перевода\\n"\n',
        '        f"/dub <URL> — 🎬 VoxCPM2-дубляж из готового перевода\\n"\n',
    )


def main() -> None:
    patch_revision_storage()
    patch_docx_order()
    patch_preflight_validation()
    patch_privileged_menu()
    patch_help_literal()
    print("approved-dub hardening applied")


if __name__ == "__main__":
    main()
