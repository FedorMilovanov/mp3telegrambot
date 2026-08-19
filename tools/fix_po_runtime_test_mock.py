from pathlib import Path

path = Path(__file__).resolve().parents[1] / "tests" / "test_youtube_po_token_runtime.py"
text = path.read_text(encoding="utf-8")
old = '    monkeypatch.setattr(po.metadata, "version", lambda _name: "1.3.1")\n'
new = '''    def installed_version(name: str) -> str:\n        if name == po.BGUTIL_DISTRIBUTION:\n            return "1.3.1"\n        raise metadata.PackageNotFoundError(name)\n\n    monkeypatch.setattr(po.metadata, "version", installed_version)\n'''
count = text.count(old)
if count != 1:
    raise RuntimeError(f"expected one PO metadata mock, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("PO_RUNTIME_TEST_MOCK_FIXED")
