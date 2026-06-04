from pathlib import Path

import yaml

from scheduler.annual_rules import constraints_from_config as annual_constraints_from_config
from scheduler.main import _rule_handlers
from scheduler.standing_rules import constraints_from_config as standing_constraints_from_config


ROOT = Path(__file__).resolve().parents[1]


# NOTE: Structural snapshot tests of the tutorial example configs
# (stanford-fellowship.yaml, example-2025-2026.yaml) were removed — they asserted
# a pre-restructuring shape and drifted whenever the examples were tuned for
# feasibility (commit 71de380). The two tests below still guard the things that
# matter: every rule kind in the examples has an optimizer handler, and the
# tutorial references real commands/paths.


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
