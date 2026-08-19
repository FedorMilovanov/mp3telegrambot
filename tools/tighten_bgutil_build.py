from pathlib import Path

path = Path(__file__).resolve().parents[1] / "tools" / "ensure_bgutil_provider.py"
text = path.read_text(encoding="utf-8")
old = '''        _run([npm, "ci"], cwd=server)\n        npx = shutil.which("npx") or shutil.which("npx.cmd")\n        if not npx:\n            raise ProvisionError("npx не найден после установки Node.js/npm")\n        _run([npx, "tsc"], cwd=server)\n'''
new = '''        _run([npm, "ci"], cwd=server)\n        tsc_name = "tsc.cmd" if os.name == "nt" else "tsc"\n        tsc = server / "node_modules" / ".bin" / tsc_name\n        if not tsc.is_file():\n            raise ProvisionError(\n                "bgutil npm ci не установил pinned local TypeScript compiler"\n            )\n        _run([str(tsc)], cwd=server)\n'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f"expected one bgutil TypeScript build block, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("BGUTIL_LOCAL_TSC_ONLY")
