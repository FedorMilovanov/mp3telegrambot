from pathlib import Path

path = Path(__file__).with_name("apply_audit_hardening_once.py")
text = path.read_text(encoding="utf-8")
old = "'''        for client in GEMINI_CLIENTS:\\n            if success:\\n                break\\n            for attempt in range(3):\\n'''"
new = "'''            for client in GEMINI_CLIENTS:\\n                if success:\\n                    break\\n                for attempt in range(3):\\n'''"
count = text.count(old)
if count != 1:
    raise RuntimeError(f"expected one patcher indentation target, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("AUDIT_PATCHER_INDENT_FIXED")
