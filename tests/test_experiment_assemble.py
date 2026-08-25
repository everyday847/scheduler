"""Golden-equivalence tests for the shared experiment.assemble_config().

The new unified config-assembly must produce a config behaviorally identical to
the legacy run_v3_optimize_wb6.load_config() (the duplicated logic it replaces),
so the entry-point consolidation is provably behavior-preserving for wb6.
"""

from __future__ import annotations

from pathlib import Path

import pytest


REPO = Path("/cv/scratch/u/watkina6/scheduler")
WB6 = REPO / "workbook_partial_input6.xlsx"
ANNUAL = REPO / "config/annual/my-2026-2027-v3.yaml"
STANDING = REPO / "config/standing/stanford-fellowship-v3.yaml"

pytestmark = pytest.mark.skipif(
    not WB6.exists(), reason="wb6 workbook not present"
)


def _legacy_config():
    import run_v3_optimize_wb6 as wb6
    config, annual = wb6.load_config()
    return config, annual


def _new_config():
    from parafrost_scheduler.experiment import assemble_config
    return assemble_config(WB6, annual_path=ANNUAL, standing_path=STANDING, verbose=False)


class TestAssembleConfigGoldenEquivalence:
    def test_locked_assignments_match(self):
        legacy, _ = _legacy_config()
        new, _ = _new_config()
        assert new.locked_assignments == legacy.locked_assignments

    def test_fellow_groups_and_shifts_match(self):
        legacy, _ = _legacy_config()
        new, _ = _new_config()
        assert new.fellow_groups == legacy.fellow_groups
        assert new.shifts == legacy.shifts

    def test_key_flags_match(self):
        legacy, _ = _legacy_config()
        new, _ = _new_config()
        assert new.night_hard_criteria == legacy.night_hard_criteria
        assert new.weekend_consecutive_hard == legacy.weekend_consecutive_hard
        assert new.weekend_night_sunday_hard == legacy.weekend_night_sunday_hard
        assert new.num_weeks == legacy.num_weeks

    def test_weekend_config_ranges_and_eow_match(self):
        legacy, _ = _legacy_config()
        new, _ = _new_config()
        lw, nw = legacy.weekend_config, new.weekend_config
        assert nw.ncc_ranges == lw.ncc_ranges
        assert nw.ncc_group_sums == lw.ncc_group_sums
        assert nw.every_other_weekend_fellows == lw.every_other_weekend_fellows
        assert nw.ncc_totals == lw.ncc_totals

    def test_constraint_count_matches(self):
        legacy, _ = _legacy_config()
        new, _ = _new_config()
        # Same number of semantic constraints (the standing+annual rule set,
        # with NCC Team Cap softened and specific_assignments injected).
        assert len(new.constraints) == len(legacy.constraints)
