#!/usr/bin/env python3
"""AUDIT R25 (живой лог + скриншот py3.13 Application Error):
`thumb_buf = open(path,"rb"); thumb_buf.name = path.name` падает на
Python 3.13 — атрибут `.name` у BufferedReader стал read-only. В логе это
были десятки `title poster error: attribute 'name' ... is not writable` и
`snapshot error: ...`, из-за чего Shorts/Clips/Montage уходили БЕЗ обложки.

Фикс: `InputFile(path.read_bytes(), filename=path.name)` — байты сразу в
памяти, файловый handle не держим, `.name` не трогаем.
"""
import re
from pathlib import Path

import pytest

_FILES = [
    "pipelines/montage.py",
    "pipelines/shorts.py",
    "pipelines/clips.py",
]

_BAD_NAME_ASSIGN = re.compile(r"\bthumb_buf\.name\s*=")
_BAD_HANDLE_NAME = re.compile(r"=\s*open\([^)]*\)\s*\n\s*\w+\.name\s*=")


@pytest.mark.parametrize("path", _FILES)
def test_no_readonly_name_assignment_on_thumbnail(path):
    src = Path(path).read_text(encoding="utf-8")
    assert not _BAD_NAME_ASSIGN.search(src), (
        f"{path}: осталось thumb_buf.name = ... (падает на py3.13)"
    )


@pytest.mark.parametrize("path", _FILES)
def test_thumbnail_built_via_inputfile(path):
    src = Path(path).read_text(encoding="utf-8")
    assert "from telegram import InputFile" in src
    assert "InputFile(" in src and "read_bytes()" in src


def test_inputfile_roundtrip_smoke(tmp_path):
    """InputFile(bytes, filename=...) реально принимает байты и имя —
    именно так теперь строится обложка."""
    from telegram import InputFile

    p = tmp_path / "poster.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0jpegdata")
    inp = InputFile(p.read_bytes(), filename=p.name)
    assert inp.filename == "poster.jpg"
