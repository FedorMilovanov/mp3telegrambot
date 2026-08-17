#!/usr/bin/env python3
from pathlib import Path

ROOTS = [Path("core"), Path("services"), Path("pipelines"), Path("handlers"), Path("tests")]

for root in ROOTS:
    if not root.exists():
        continue
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        text = text.replace("gemini-3.6-flash", "gemini-3.7-flash")
        text = text.replace("Gemini 3.6", "Gemini 3.7")
        path.write_text(text, encoding="utf-8")

# Utility work stays on 3.5 Flash Lite and must not fall back to ordinary 3.5
# or to the semantic 3.7 route.
for name, dead_constant, old_assignment, old_summary, new_summary in [
    (
        "services/gemini_max_quality.py",
        '_LIGHT_FALLBACK_MODEL = "gemini-3.5-flash"\n',
        'os.environ["GEMINI_LIGHT_FALLBACK_MODELS"] = _LIGHT_FALLBACK_MODEL',
        'f"utility={_LIGHT_MODEL}->{_LIGHT_FALLBACK_MODEL}/minimal; "',
        'f"utility={_LIGHT_MODEL}/minimal/no-fallback; "',
    ),
    (
        "services/livedub_quality_runtime.py",
        '_UTILITY_FALLBACK_MODEL = "gemini-3.5-flash"\n',
        'os.environ["GEMINI_LIGHT_FALLBACK_MODELS"] = _UTILITY_FALLBACK_MODEL',
        'f"utility={_LIGHT_MODEL}->{_UTILITY_FALLBACK_MODEL}/no-main-fallback"',
        'f"utility={_LIGHT_MODEL}/minimal/no-fallback"',
    ),
]:
    path = Path(name)
    text = path.read_text(encoding="utf-8")
    text = text.replace(old_assignment, 'os.environ["GEMINI_LIGHT_FALLBACK_MODELS"] = ""')
    text = text.replace(old_summary, new_summary)
    text = text.replace(dead_constant, "")
    text = text.replace("publication=3.6/high", "publication=3.7/high")
    path.write_text(text, encoding="utf-8")

# Factory: 503 after bounded retries rotates across every configured API client.
path = Path("services/shorts_factory_capacity_runtime.py")
text = path.read_text(encoding="utf-8")
old = '''            if action == "capacity":
                capacity_overload = True
                await capacity.safe_status(
                    status_msg,
                    "⚠️ Gemini 3.7 вернула 503/high demand после ограниченных "
                    "повторов текущего HIGH-прохода. Не загружаю то же "
                    "analysis-аудио на остальные ключи: качество не понижаю, "
                    "retry-кэш сохранён.",
                )
                break
'''
new = '''            if action == "capacity":
                capacity_overload = True
                await capacity.safe_status(
                    status_msg,
                    f"⚠️ Gemini 3.7 вернула 503/high demand на ключе "
                    f"{index}/{len(clients)} после ограниченных повторов HIGH-прохода. "
                    "Переключаюсь на следующий ключ без понижения модели…",
                )
                continue
'''
if old not in text:
    raise RuntimeError("Factory capacity block not found")
text = text.replace(old, new, 1)
old = '''            "Ограниченные повторы текущего HIGH-прохода исчерпаны; перебор "
            "остальных API-ключей остановлен, чтобы не повторять дорогую "
            "загрузку того же analysis-аудио. Качество не понижено: 3.5/2.x "
            "не использовались. Analysis-аудио сохранено в retry-кэше примерно на "
'''
new = '''            "Ограниченные повторы HIGH-прохода и все настроенные API-ключи/клиенты "
            "исчерпаны. Качество не понижено: 3.6/3.5/Lite не использовались. "
            "Analysis-аудио сохранено в retry-кэше примерно на "
'''
if old not in text:
    raise RuntimeError("Factory capacity final message not found")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

# Tests that intentionally assert the source-owned Lite-only utility route.
for path in Path("tests").rglob("*.py"):
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        'assert os.environ["GEMINI_LIGHT_FALLBACK_MODELS"] == "gemini-3.5-flash"',
        'assert os.environ["GEMINI_LIGHT_FALLBACK_MODELS"] == ""',
    )
    text = text.replace("3.5-lite->3.5", "3.5-lite/minimal/no-fallback")
    path.write_text(text, encoding="utf-8")

# Fail closed if the contract is only partially migrated.
maxq = Path("services/gemini_max_quality.py").read_text(encoding="utf-8")
qa = Path("services/gemini_qa_policy.py").read_text(encoding="utf-8")
livedub = Path("services/livedub_quality_runtime.py").read_text(encoding="utf-8")
factory = Path("services/shorts_factory_candidates.py").read_text(encoding="utf-8")
capacity = Path("services/shorts_factory_capacity_runtime.py").read_text(encoding="utf-8")
assert '_HEAVY_MODEL = "gemini-3.7-flash"' in maxq
assert '_PRIMARY_MODEL = "gemini-3.7-flash"' in qa
assert '_PRIMARY_MODEL = "gemini-3.7-flash"' in livedub
assert 'GEMINI_LIGHT_FALLBACK_MODELS"] = ""' in maxq
assert 'GEMINI_LIGHT_FALLBACK_MODELS"] = ""' in livedub
assert 'DEFAULT_SHORTS_FACTORY_MODEL = "gemini-3.7-flash"' in factory
assert "Переключаюсь на следующий ключ без понижения модели" in capacity
assert "все настроенные API-ключи/клиенты" in capacity
assert "Gemini 3.6 MAX" not in capacity
print("Gemini 3.7 quality routing migration OK")
