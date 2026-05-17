from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_standing_rule_example_is_valid_yaml():
    config = yaml.safe_load((ROOT / "config/standing/stanford-fellowship.yaml").read_text())

    assert "block_rules" in config
    assert "max_consecutive" in config
    assert config["swing_deficit"]["shift"] == "Swing"
    assert any(rule["name"] == "ncc_four_week_blocks" for rule in config["block_rules"])


def test_annual_request_example_is_valid_yaml():
    request = yaml.safe_load((ROOT / "config/annual/example-2025-2026.yaml").read_text())

    assert request["jr_fellows"] == ["NCC Raya", "NCC Joseph"]
    assert request["fellow_week_pairs"]["NCC Prash"][:3] == [1, 7, 25]
    assert "annual_rules" in request
    assert request["annual_rules"]["vacation_request_policy"]["hard_request_count"] == 3


def test_command_line_tutorial_references_real_commands():
    tutorial = (ROOT / "docs/tutorials/command-line-scheduling.md").read_text()

    assert "uv run scheduler default-request" in tutorial
    assert "uv run scheduler solve" in tutorial
    assert "config/standing/stanford-fellowship.yaml" in tutorial
    assert "config/annual/example-2025-2026.yaml" in tutorial
    assert "src/scheduler/main.py" in tutorial

