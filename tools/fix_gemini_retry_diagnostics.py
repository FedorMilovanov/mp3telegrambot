from pathlib import Path

path = Path(__file__).resolve().parents[1] / "services" / "gemini_analyze.py"
text = path.read_text(encoding="utf-8")
old = '''                        if _is_quota or _is_overload or _is_timeout:\n                            logger.warning(\n                                f"Gemini {'квота' if _is_quota else ('timeout' if _is_timeout else '503/disconnect')}: "\n                                f"{type(e).__name__}: {str(e)[:200]} -- пробую следующий ключ..."\n                            )\n                            last_err = e\n'''
new = '''                        if _is_quota or _is_overload or _is_timeout:\n                            if _is_overload and attempt < 2:\n                                _retry_action = "повторяю тот же ключ и тот же upload"\n                            else:\n                                _retry_action = "переключаюсь на следующий ключ"\n                            logger.warning(\n                                f"Gemini {'квота' if _is_quota else ('timeout' if _is_timeout else '503/disconnect')}: "\n                                f"{type(e).__name__}: {str(e)[:200]} -- {_retry_action}"\n                            )\n                            last_err = e\n'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f"expected one Gemini retry diagnostic block, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("GEMINI_RETRY_DIAGNOSTICS_FIXED")
