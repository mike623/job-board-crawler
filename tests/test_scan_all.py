from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "reed_crawler"))

import scan_all


# ---- the config decides which boards run ----

def test_a_disabled_board_is_not_scanned():
    config = {"boards": {"reed": {"enabled": True}, "indeed": {"enabled": False}}}
    assert scan_all.enabled_boards(config) == ["reed"]


def test_a_board_missing_from_the_config_is_not_scanned():
    assert scan_all.enabled_boards({"boards": {"reed": {"enabled": True}}}) == ["reed"]
    assert scan_all.enabled_boards({}) == []


def test_every_known_board_can_be_enabled():
    config = {"boards": {b: {"enabled": True} for b in scan_all.COMMANDS}}
    assert set(scan_all.enabled_boards(config)) == set(scan_all.COMMANDS)


# ---- the command handed to each subprocess ----

def test_the_config_path_reaches_the_board_and_is_not_hardcoded():
    for board in scan_all.COMMANDS:
        command = scan_all.command_for(board, "/somewhere/else.yml", None)
        assert "config.yml" not in command
        assert command[command.index("--config") + 1] == "/somewhere/else.yml"


def test_a_limit_is_passed_through_only_when_asked_for():
    assert "--limit" not in scan_all.command_for("reed", "config.yml", None)
    assert scan_all.command_for("reed", "config.yml", 1)[-2:] == ["--limit", "1"]


def test_no_board_is_scanned_with_a_flag_that_overrides_the_config():
    # --allow-disabled exists for manual smoke tests; the cron must not use it.
    for command in scan_all.COMMANDS.values():
        assert "--allow-disabled" not in command


def test_the_dashboard_runs_the_same_commands_as_the_cron():
    from dashboard import scans

    assert scans.COMMANDS is scan_all.COMMANDS
