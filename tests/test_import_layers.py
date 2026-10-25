"""Executable architecture boundaries and their temporary burn-down policy."""

from __future__ import annotations

import configparser
import logging

from tools.check_import_layers import CONFIG, ROOT, check


def test_production_layer_contract_passes_against_the_real_graph() -> None:
    """This runs import-linter; a configuration-only assertion is insufficient."""
    assert check(ROOT) == 0


def test_contract_check_preserves_the_callers_logging(caplog) -> None:
    """The linter must run in a subprocess, not disable pytest's loggers."""
    logger = logging.getLogger("tests.import_layers")
    with caplog.at_level(logging.INFO, logger=logger.name):
        assert check(ROOT) == 0
        logger.info("caller logging survives import-linter")
    assert "caller logging survives import-linter" in caplog.text


def test_legacy_ignores_are_exact_and_stale_entries_fail() -> None:
    parser = configparser.ConfigParser()
    with (ROOT / CONFIG).open(encoding="utf-8") as handle:
        parser.read_file(handle)
    contract = parser["importlinter:contract:production-layers"]
    ignores = [line.strip() for line in contract["ignore_imports"].splitlines() if line.strip()]

    assert ignores, "Remove the empty option as well when the burn-down is complete"
    assert all("->" in item and "*" not in item for item in ignores)
    assert len(ignores) == len(set(ignores))
    assert contract["unmatched_ignore_imports_alerting"] == "error"
