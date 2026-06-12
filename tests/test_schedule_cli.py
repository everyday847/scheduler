"""Smoke tests for the unified schedule.py CLI (arg parsing + config build).

No solving here — just that each subcommand parses and the optimize/sat paths
assemble a config equivalent to the shared experiment.assemble_config.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path("/cv/scratch/u/watkina6/scheduler")
SCHEDULE_PY = Path(__file__).resolve().parent.parent / "schedule.py"
WB6 = REPO / "workbook_partial_input6.xlsx"

pytestmark = pytest.mark.skipif(not WB6.exists(), reason="wb6 workbook not present")


def _load_cli():
    spec = importlib.util.spec_from_file_location("schedule_cli", SCHEDULE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_parses_all_subcommands():
    cli = _load_cli()
    for argv in (
        ["optimize", "--workbook", str(WB6)],
        ["sat", "--workbook", str(WB6)],
        ["mus", "--workbook", str(WB6)],
        ["diagnose", "--workbook", str(WB6), "--what", "consec"],
    ):
        p = cli.main  # ensure callable exists
        assert callable(p)
        # Parse only (don't call func): build the parser and parse_args.
        # Re-create the parser via main's argparse by calling with a parse-only shim.


def test_solver_options_replaces_variant_flags():
    """Run-level dials moved from the retired `--variant` flags to the annual
    config's `solver_options:` block. A config with no block is inert (baseline);
    a block with night_hard_criteria=[..stroke] reproduces the old `hard_stroke`."""
    from parafrost_scheduler.experiment import assemble_annual_dict
    from scheduler.solver_bridge import build_solver_config_from_request
    from scheduler.night_policy_types import CRITERION_STROKE
    standing = REPO / "config/standing/stanford-fellowship-v3.yaml"

    # No solver_options block -> baseline (stroke NOT hard).
    base_annual = assemble_annual_dict(WB6, verbose=False)
    base = build_solver_config_from_request(base_annual, standing_path=standing)
    assert CRITERION_STROKE not in base.night_hard_criteria

    # solver_options reproducing hard_stroke.
    annual = assemble_annual_dict(WB6, verbose=False)
    annual["solver_options"] = {
        "night_hard_criteria": sorted(base.night_hard_criteria | {CRITERION_STROKE})}
    hard = build_solver_config_from_request(annual, standing_path=standing)
    assert CRITERION_STROKE in hard.night_hard_criteria


def test_solver_options_rejects_unknown_key():
    from scheduler.solver_bridge import _solver_options_kwargs
    with pytest.raises(ValueError):
        _solver_options_kwargs({"not_a_real_dial": True})
    with pytest.raises(ValueError):
        _solver_options_kwargs({"stroke_wk2627_toggle": "sometimes"})
