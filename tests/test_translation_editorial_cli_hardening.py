from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from tools import translation_editorial as cli


def test_candidate_file_shape_fails_closed_instead_of_silently_dropping_entries(
    tmp_path: Path,
) -> None:
    bad_root = tmp_path / "bad-root.json"
    bad_root.write_text(json.dumps({"shorts": {"start_seconds": 1}}), encoding="utf-8")
    with pytest.raises(ValueError, match="shorts candidates must be a list"):
        cli._candidate_groups(bad_root)

    bad_item = tmp_path / "bad-item.json"
    bad_item.write_text(json.dumps({"shorts": [{"start_seconds": 1}, "oops"]}), encoding="utf-8")
    with pytest.raises(ValueError, match=r"shorts\[2\] candidate must be an object"):
        cli._candidate_groups(bad_item)


def test_manual_media_id_is_sanitized_before_becoming_a_local_filename() -> None:
    safe = cli._safe_media_id("../../outside\\NUL:video")

    assert "/" not in safe
    assert "\\" not in safe
    assert ":" not in safe
    assert safe


def test_review_template_writer_never_deletes_a_fileexists_winner() -> None:
    source = inspect.getsource(cli._write_review_template)

    assert "except FileExistsError" in source
    assert "if created" in source


def test_repair_cli_never_unlinks_unknown_final_paths_in_generic_error_cleanup() -> None:
    source = inspect.getsource(cli._cmd_repair)

    assert "output_path.unlink" not in source
    assert "provenance_path.unlink" not in source
    assert "incomplete output/provenance pair" in source
