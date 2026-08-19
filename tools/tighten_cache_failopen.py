from pathlib import Path

path = Path(__file__).resolve().parents[1] / "services" / "shorts_factory_retry_cache.py"
text = path.read_text(encoding="utf-8")
old = "    except (OSError, RuntimeError, ValueError) as exc:\n"
new = "    except OSError as exc:\n"
count = text.count(old)
if count != 1:
    raise RuntimeError(f"expected one cache fail-open exception boundary, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("CACHE_FAILOPEN_BOUNDARY_TIGHTENED")
