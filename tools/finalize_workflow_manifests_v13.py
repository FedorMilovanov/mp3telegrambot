#!/usr/bin/env python3
from pathlib import Path


def update(path: str, replacements: tuple[tuple[str, str], ...]) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    for old, new in replacements:
        count = text.count(old)
        if count == 0:
            raise RuntimeError(f"v13 expected text missing in {path}: {old!r}")
        text = text.replace(old, new)
    target.write_text(text, encoding="utf-8")


update(
    ".github/workflows/ci.yml",
    (("          tests/test_gemini_startup_diagnostics.py\n", ""),),
)

update(
    ".github/workflows/dub-studio-checks.yml",
    (
        (
            "handlers/dub_health handlers/dub_wizard.py handlers/dub_wizard handlers/dub_commands.py",
            "handlers/dub_health.py handlers/dub_wizard.py handlers/dub_commands.py",
        ),
        ("          handlers/dub_health\n", "          handlers/dub_health.py\n"),
        ("          handlers/dub_wizard\n", ""),
    ),
)

update(
    ".github/workflows/voxcpm2-windows.yml",
    (
        ("uses: actions/checkout@v4", "uses: actions/checkout@v6"),
        ("uses: actions/setup-python@v5", "uses: actions/setup-python@v6"),
        ('      - "handlers/dub_health/**"\n', '      - "handlers/dub_health.py"\n'),
        ('      - "handlers/dub_wizard/**"\n', ""),
        (
            "handlers/dub_health handlers/dub_wizard.py handlers/dub_wizard tools/voxcpm2",
            "handlers/dub_health.py handlers/dub_wizard.py tools/voxcpm2",
        ),
        ("          handlers/dub_health\n", "          handlers/dub_health.py\n"),
        ("          handlers/dub_wizard\n", ""),
    ),
)

# Fail closed if any retired hard-coded workflow path survives.
checks = {
    ".github/workflows/ci.yml": ("tests/test_gemini_startup_diagnostics.py",),
    ".github/workflows/dub-studio-checks.yml": (
        "handlers/dub_health\n",
        "handlers/dub_wizard\n",
    ),
    ".github/workflows/voxcpm2-windows.yml": (
        "handlers/dub_health/**",
        "handlers/dub_wizard/**",
        "handlers/dub_health\n",
        "handlers/dub_wizard\n",
        "actions/checkout@v4",
        "actions/setup-python@v5",
    ),
}
for path, retired in checks.items():
    text = Path(path).read_text(encoding="utf-8")
    for marker in retired:
        if marker in text:
            raise RuntimeError(f"v13 retired workflow marker remains: {path}: {marker}")

print("v13 canonical workflow manifests applied")
