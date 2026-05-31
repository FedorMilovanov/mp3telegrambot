import os

def patch_file(fpath, old_text, new_text):
    with open(fpath, "r", encoding="utf-8") as f:
        content = f.read()
    if old_text not in content:
        print(f"⚠️ Текст не найден в: {fpath}")
        return False
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content.replace(old_text, new_text))
    print(f"✅ Успешно пропатчен: {fpath}")
    return True

print("Устранение остатков компрессии в промпте...\n")

patch_file("core/prompts.py",
"""Твоя задача — сохранить почти весь смысловой ход автора""",
"""Твоя задача — сохранить АБСОЛЮТНО ВЕСЬ смысловой ход автора""")

patch_file("core/prompts.py",
"""- речь автора, переданная 1 в 1, включая развернутые примеры, иллюстрации и ход мысли,""",
"""- речь автора, переданная 1 в 1 без изъятий, включая абсолютно все развернутые примеры, иллюстрации и малейшие детали,""")

print("\n🎉 Готово!")