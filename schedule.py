#!/usr/bin/env python
"""Unified scheduler CLI — one entry point, one documented behavior contract.

Replaces the run_v3_optimize*/heavy_opt_*/sat_check_* family of near-duplicate
scripts. All subcommands assemble their config via the SINGLE
parafrost_scheduler.experiment.assemble_config (workbook import + SHIFT_MAP +
locks + specific-assignment pins + NCC-Team-Cap softening + standing merge).

    python schedule.py optimize --workbook WB.xlsx [--max-seconds S] [--preview L]
    python schedule.py sat      --workbook WB.xlsx [--variant V]
    python schedule.py mus      --workbook WB.xlsx
    python schedule.py diagnose --workbook WB.xlsx --what consec

OPTIMIZE DISK-WRITE CONTRACT (important — read this):
  optimize_stream runs RoundingSat in budget SLICES (the --preview values, then
  one final slice with all remaining time). The schedule is written to disk only
  when a slice RETURNS. So if the first feasible incumbent appears AFTER the
  preview slices, nothing lands on disk until the final slice ends. The default
  previews include 600s + 1800s returning checkpoints so a long run still writes
  intermediate schedules. The live `c bounds` log lines are bound updates only —
  they do NOT mean the schedule was written.

Run heavy jobs on Slurm (RoundingSat is single-threaded): -A prescient1 -p defq -n 1.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

from parafrost_scheduler.experiment import (
    DEFAULT_ANNUAL, DEFAULT_STANDING, assemble_config,
    solution_to_parsed, swap_ncc_weekend_roles, write_csv,
)
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_encoder import (
    build_full_schedule_opb,
    soft_penalty_breakdown,
)
from parafrost_scheduler.schedule_optimizer import optimize_stream
from parafrost_scheduler.workbook import write_schedule_workbook
from schedule_rules.criteria.night_gating import configured_night_gating
from schedule_rules.criteria.weekend_gating import configured_weekend_gating
from schedule_rules.criteria.weekend_night import configured_weekend_night
from scheduler.night_policy_types import (
    CRITERION_ANAESTHESIA, CRITERION_SUNDAY_FOLLOWING, CRITERION_STROKE,
)

ROUNDINGSAT = Path("vendor/roundingsat/build/roundingsat")
# Workbook-coloring hard criteria (matches the shipped night_hard_criteria plus
# sunday_following, which is now soft for the solve but still shown un-red here).
# weekend_night_friday is the coalesced Friday rule (was friday_weekend_ncc1) —
# the NCC1 case is hard, so it is shown un-red like the other hard criteria.
_COLOR_HARD = frozenset({CRITERION_ANAESTHESIA, "weekend_night_friday",
                         CRITERION_SUNDAY_FOLLOWING})


def _harden_sunday_weekend_night(config):
    """Return a config whose Sunday weekend_night rule is HARD (role_strengths +
    eligibility) — the `hard_sunday` variant: the Sunday-night holder MUST hold
    Weekend Stroke, and a non-stroke-eligible fellow is forbidden Sunday night."""
    new_constraints = []
    for c in config.constraints:
        if (c.kind == "weekend_night"
                and c.params.get("criterion") == "weekend_night_sunday"):
            params = {**c.params}
            params["role_strengths"] = [
                {**rs, "hard": True} for rs in params["role_strengths"]]
            if params.get("eligibility"):
                params["eligibility"] = {**params["eligibility"], "hard": True}
            c = dataclasses.replace(c, params=params)
        new_constraints.append(c)
    return dataclasses.replace(config, constraints=new_constraints)


def _without_stroke_role_weight(config):
    """Return a config whose weekend_role_mismatch constraint no longer carries the
    heavier Weekend-Stroke role weight (reverting Stroke to the base mismatch
    weight) — the `no_stroke_align` isolation variant."""
    new_constraints = []
    for c in config.constraints:
        if (c.kind == "weekend_gating"
                and c.params.get("criterion") == "weekend_role_mismatch"
                and c.params.get("role_weights")):
            rw = {k: v for k, v in c.params["role_weights"].items()
                  if k != "Weekend Stroke"}
            c = dataclasses.replace(c, params={**c.params, "role_weights": rw})
        new_constraints.append(c)
    return dataclasses.replace(config, constraints=new_constraints)


def _apply_variant(config, variant: str):
    if variant in (None, "baseline"):
        return config
    if variant == "hard_stroke":
        return dataclasses.replace(
            config, night_hard_criteria=config.night_hard_criteria | {CRITERION_STROKE})
    if variant == "hard_sunday":
        return _harden_sunday_weekend_night(config)
    if variant == "hard_consec":
        return dataclasses.replace(config, weekend_consecutive_hard=True)
    if variant == "aan_hard":
        # NH AAN-week call avoidance becomes HARD (no night/weekend that week).
        return dataclasses.replace(config, nh_aan_week_call_hard=True)
    if variant == "no_stroke_align":
        # Revert Weekend Stroke to the base mismatch weight (the former dedicated
        # Stroke-alignment nudge is now the +40 folded into the weekend_role_mismatch
        # role_weight; dropping it leaves only the shared mismatch-20 penalty).
        return _without_stroke_role_weight(config)
    if variant == "abpn_block":
        # ABPN night-block + dual-stroke-Helena preference + wk26/27 on-off toggle.
        return dataclasses.replace(
            config, abpn_night_block=True, dual_stroke_helena="Helena Xeros",
            stroke_wk2627_toggle="hard")
    raise SystemExit(f"unknown variant: {variant!r}")


def _load(args):
    config, annual = assemble_config(
        Path(args.workbook), annual_path=Path(args.annual),
        standing_path=Path(args.standing), verbose=True)
    config = _apply_variant(config, getattr(args, "variant", "baseline"))
    if getattr(args, "relax_locks", False):
        config = dataclasses.replace(config, relax_locked_ncc_trio=True)
    return config, annual


def _write_outputs(sol, config, out_prefix: Path):
    write_csv(sol, out_prefix.with_suffix(".csv"))
    parsed = solution_to_parsed(sol, list(sol.weekly_assignments.keys()))
    # The configured gating-criterion registries — the same instances the encoder
    # used — so the workbook colorer cannot drift from the solver.
    night_gating = configured_night_gating(config.constraints)
    weekend_gating = configured_weekend_gating(config.constraints)
    weekend_night = configured_weekend_night(config.constraints)
    write_schedule_workbook(
        parsed, sol.night_solution, sol.weekend_solution,
        out_prefix.parent / f"{out_prefix.name}_workbook.xlsx",
        hard_criteria=_COLOR_HARD,
        backup_solution=getattr(sol, "backup_solution", None),
        fellow_groups=config.fellow_groups,
        night_gating=night_gating, weekend_gating=weekend_gating,
        weekend_night=weekend_night)
    # Guarded NCC weekend-role swap -> second workbook (a no-op when the model
    # already aligns NCC weekday/weekend roles).
    swapped = swap_ncc_weekend_roles(parsed, sol.weekend_solution, sol.night_solution)
    write_schedule_workbook(
        parsed, sol.night_solution, swapped,
        out_prefix.parent / f"{out_prefix.name}_workbook_swapped.xlsx",
        hard_criteria=_COLOR_HARD,
        backup_solution=getattr(sol, "backup_solution", None),
        fellow_groups=config.fellow_groups,
        night_gating=night_gating, weekend_gating=weekend_gating,
        weekend_night=weekend_night)


def cmd_optimize(args):
    config, annual = _load(args)
    out_prefix = Path(args.out_prefix or f"output_{Path(args.workbook).stem}")
    preview = tuple(float(x) for x in args.preview.split(",") if x.strip())
    print(f"weekend_consecutive_hard={config.weekend_consecutive_hard} "
          f"night_hard_criteria={sorted(config.night_hard_criteria)} "
          f"relax_locked_ncc_trio={config.relax_locked_ncc_trio}", flush=True)
    print(f"preview slices (returning, write-to-disk) = {preview}", flush=True)
    runner = RoundingSatRunner(ROUNDINGSAT)
    best = None
    for step in optimize_stream(config, runner, preview_seconds=preview,
                                max_seconds=args.max_seconds, echo_progress=True):
        best = step.solution
        gap = (f"  lb={step.lower_bound} gap={step.total_penalty - step.lower_bound}"
               if step.lower_bound is not None else "")
        tag = "  [OPTIMAL]" if step.optimal else ""
        print(f"  [{step.elapsed:6.1f}s] total={step.total_penalty:6d} "
              f"weekly={step.weekly_penalty:5d} weekend/night={step.call_penalty:5d}{gap}{tag}",
              flush=True)
        _write_outputs(step.solution, config, out_prefix)
    if best is None:
        print("INFEASIBLE!")
        return 1
    print(f"\nBest penalty: {best.soft_penalty}  ->  {out_prefix}.csv / {out_prefix}_workbook.xlsx")
    return 0


def cmd_sat(args):
    config, annual = _load(args)
    runner = RoundingSatRunner(ROUNDINGSAT)
    opb, vm = build_full_schedule_opb(config, objective=True)
    res = runner.optimize(opb, time_limit=args.sat_limit)
    if not (res.satisfiable and res.assignment) and not res.proven_unsat:
        res = runner.optimize(opb, time_limit=args.unsat_limit)
    if res.satisfiable and res.assignment is not None:
        wk, cn, tot = soft_penalty_breakdown(res.assignment, vm)
        print(f"SAT  total={tot} weekly={wk} call={cn} optimal={res.optimal}")
        return 0
    print("UNSAT" if res.proven_unsat else "UNKNOWN")
    return 0


def cmd_mus(args):
    import mus_buffered_consec  # existing tool; reused as-is
    return mus_buffered_consec.main()


def cmd_diagnose(args):
    if args.what == "consec":
        import diagnose_consec_wb6  # existing diagnostic
        return 0
    raise SystemExit(f"unknown diagnose target: {args.what!r}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--annual", default=str(DEFAULT_ANNUAL))
    p.add_argument("--standing", default=str(DEFAULT_STANDING))
    sub = p.add_subparsers(dest="cmd", required=True)

    po = sub.add_parser("optimize", help="streaming optimization (see disk-write contract)")
    po.add_argument("--workbook", required=True)
    po.add_argument("--variant", default="baseline")
    po.add_argument("--max-seconds", type=float, default=21600.0)
    po.add_argument("--preview", default="8,25,90,600,1800")
    po.add_argument("--out-prefix", default=None)
    po.add_argument("--relax-locks", action="store_true",
                    help="float locked fellows' NCC1/NCC2/Swing weeks among the "
                         "trio instead of pinning the exact role")
    po.set_defaults(func=cmd_optimize)

    ps = sub.add_parser("sat", help="SAT-feasibility check")
    ps.add_argument("--workbook", required=True)
    ps.add_argument("--variant", default="baseline")
    ps.add_argument("--sat-limit", type=float, default=400.0)
    ps.add_argument("--unsat-limit", type=float, default=1500.0)
    ps.add_argument("--relax-locks", action="store_true",
                    help="float locked fellows' NCC1/NCC2/Swing weeks among the "
                         "trio instead of pinning the exact role")
    ps.set_defaults(func=cmd_sat)

    pm = sub.add_parser("mus", help="minimal-unsatisfiable-subset extraction")
    pm.add_argument("--workbook", required=True)
    pm.set_defaults(func=cmd_mus)

    pd = sub.add_parser("diagnose", help="run a named diagnostic")
    pd.add_argument("--workbook", required=True)
    pd.add_argument("--what", required=True, choices=["consec"])
    pd.set_defaults(func=cmd_diagnose)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
