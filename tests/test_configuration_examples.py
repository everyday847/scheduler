from pathlib import Path

import yaml

from scheduler.annual_rules import constraints_from_config as annual_constraints_from_config
from scheduler.main import _rule_handlers
from scheduler.standing_rules import constraints_from_config as standing_constraints_from_config


ROOT = Path(__file__).resolve().parents[1]


def test_standing_rule_example_is_valid_yaml():
    config = yaml.safe_load((ROOT / "config/standing/stanford-fellowship.yaml").read_text())

    assert "rules" in config
    assert any(rule["name"] == "ncc_four_week_blocks" for rule in config["rules"])
    assert any(rule["kind"] == "ncc_coverage" for rule in config["rules"])
    assert all("active" in rule for rule in config["rules"])


def test_ccm_service_is_defined_declaratively_in_standing_yaml():
    config = yaml.safe_load((ROOT / "config/standing/stanford-fellowship.yaml").read_text())

    ccm_rule = next(rule for rule in config["rules"] if rule["name"] == "ccm_service_profile")

    assert ccm_rule["kind"] == "service_profile"
    assert ccm_rule["fellow_groups"] == ["CCM"]
    assert "MICU" in ccm_rule["zero_shifts"]
    assert {"shifts": ["NCC1", "NCC2"], "relation": "exactly", "weeks": 3} in ccm_rule["totals"]
    assert {"shifts": ["Swing"], "relation": "exactly", "weeks": 1} in ccm_rule["totals"]
    assert ccm_rule["active_blocks"] == [
        {
            "name": "ccm_ncc_block",
            "block_size": 4,
            "trigger_shifts": ["NCC1", "NCC2", "Swing"],
            "counts": [
                {"shifts": ["NCC1", "NCC2", "Swing"], "relation": "exactly", "weeks": 4},
                {"shifts": ["Swing"], "relation": "exactly", "weeks": 1},
            ],
        }
    ]


def test_ncc_and_stroke_service_are_defined_declaratively_in_standing_yaml():
    config = yaml.safe_load((ROOT / "config/standing/stanford-fellowship.yaml").read_text())
    rules = {rule["name"]: rule for rule in config["rules"]}

    assert rules["ncc_jr_service_profile"]["kind"] == "service_profile"
    assert rules["ncc_jr_service_profile"]["fellow_groups"] == ["NCC_JR"]
    assert {"shifts": ["NCC1", "NCC2"], "relation": "exactly", "weeks": 9} in rules["ncc_jr_service_profile"]["totals"]
    assert {"shifts": ["MICU"], "relation": "at_least", "weeks": 20} in rules["ncc_jr_service_profile"]["totals"]
    assert "Stroke" in rules["ncc_jr_service_profile"]["zero_shifts"]

    assert rules["ncc_sr_service_profile"]["kind"] == "service_profile"
    assert rules["ncc_sr_service_profile"]["fellow_groups"] == ["NCC_SR"]
    assert {"shifts": ["NCC1", "NCC2"], "relation": "exactly", "weeks": 14} in rules["ncc_sr_service_profile"]["totals"]
    assert {"shifts": ["Swing"], "relation": "at_least", "weeks": 6} in rules["ncc_sr_service_profile"]["totals"]
    assert "SICU" in rules["ncc_sr_service_profile"]["zero_shifts"]

    assert rules["stroke_service_profile"]["kind"] == "service_profile"
    assert rules["stroke_service_profile"]["fellow_groups"] == ["STROKE"]
    assert {"shifts": ["NCC1", "NCC2"], "relation": "exactly", "weeks": 4} in rules["stroke_service_profile"]["totals"]
    assert {"shifts": ["Swing"], "relation": "exactly", "weeks": 2} in rules["stroke_service_profile"]["totals"]
    assert {"shifts": ["Stroke"], "relation": "at_least", "weeks": 11} in rules["stroke_service_profile"]["totals"]
    assert "MICU" in rules["stroke_service_profile"]["zero_shifts"]


def test_nh_service_is_defined_declaratively_in_annual_yaml():
    request = yaml.safe_load((ROOT / "config/annual/example-2025-2026.yaml").read_text())
    rules = {rule["name"]: rule for rule in request["annual_rules"]["rules"]}

    assert rules["nh_service_profile"]["kind"] == "service_profile"
    assert rules["nh_service_profile"]["fellow_groups"] == ["NH"]
    assert {"shifts": ["NCC1", "NCC2"], "relation": "exactly", "weeks": 4} in rules["nh_service_profile"]["totals"]
    assert {"shifts": ["Swing"], "relation": "exactly", "weeks": 1} in rules["nh_service_profile"]["totals"]
    assert {"shifts": ["Telestroke/Clinic"], "relation": "exactly", "weeks": 3} in rules["nh_service_profile"]["totals"]
    assert "MICU" in rules["nh_service_profile"]["zero_shifts"]


def test_annual_request_example_is_valid_yaml():
    request = yaml.safe_load((ROOT / "config/annual/example-2025-2026.yaml").read_text())

    assert request["fellow_groups"]["NCC_JR"] == ["NCC Raya", "NCC Joseph"]
    assert request["fellow_week_pairs"]["NCC Prash"][:3] == [1, 7, 25]
    assert "annual_rules" in request
    assert any(
        rule["kind"] == "vacation_request_policy" and rule["hard_request_count"] == 3
        for rule in request["annual_rules"]["rules"]
    )
    assert any(
        rule["kind"] == "specific_assignment" and rule["strength"] == "soft"
        for rule in request["annual_rules"]["rules"]
    )


def test_configuration_rule_kinds_have_optimizer_handlers():
    standing = yaml.safe_load((ROOT / "config/standing/stanford-fellowship.yaml").read_text())
    annual = yaml.safe_load((ROOT / "config/annual/example-2025-2026.yaml").read_text())
    constraints = [
        *standing_constraints_from_config(standing),
        *annual_constraints_from_config(
            annual["annual_rules"],
            fellow_week_pairs=annual["fellow_week_pairs"],
        ),
    ]

    missing = {
        constraint.kind
        for constraint in constraints
        if constraint.kind not in _rule_handlers()
    }
    assert missing == set()


def test_command_line_tutorial_references_real_commands():
    tutorial = (ROOT / "docs/tutorials/command-line-scheduling.md").read_text()

    assert "uv run scheduler default-request" in tutorial
    assert "uv run scheduler solve" in tutorial
    assert "config/standing/stanford-fellowship.yaml" in tutorial
    assert "config/annual/example-2025-2026.yaml" in tutorial
    assert "src/scheduler/main.py" in tutorial
