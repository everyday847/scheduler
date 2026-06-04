"""Experiment harness for gradually hardening soft scheduling constraints.

Tests whether currently-soft rules can be hardened (flipped soft -> hard) while
keeping the WB2 problem satisfiable, in priority order. Two phases:

  Phase A (isolation): harden ONE rule at a time, SAT-check each.
  Phase B (cumulative): walk the priority order, adding a rule only if the
      problem stays SAT; record failures and continue.

When a rule goes UNSAT, the --no-lock A/B distinguishes a real conflict from a
locked/imported-fellow artifact (a rule the locked fellows themselves violate).

This harness is READ-ONLY w.r.t. the live config YAMLs and never writes
output_v3_wb2.* — it only builds configs in memory and (optionally) emits a JSON
results file you name.

Usage examples:
  PYTHONPATH=src:new_approach/src python harden_experiment.py --phase a
  PYTHONPATH=src:new_approach/src python harden_experiment.py --only "NCC1 Coverage" --no-lock
  PYTHONPATH=src:new_approach/src python harden_experiment.py --phase both --json results.json
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from dataclasses import dataclass, asdict, field

import run_v3_optimize_wb2 as wb2
from scheduler.solver_bridge import build_solver_config_from_request
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from parafrost_scheduler.schedule_solver import (
    build_full_schedule_opb, soft_penalty_breakdown,
)

ROUNDINGSAT = wb2.ROUNDINGSAT
STANDING = wb2.STANDING

# --- The default priority order (rule name, kind-note). See the plan. --------
# Group 1: coverage (most operationally important; the real false-UNSAT risk).
# Group 2: activate-then-soft (verify SAT as soft before hardening).
# Group 3: per-fellow workload totals (already locked-guarded).
# Group 4: structural.
DEFAULT_ORDER = [
    "NCC1 Coverage",
    "NCC2 Coverage",
    "NCC/Stroke Oversight",
    "NCC Team Cap",
    "NCC Senior: NS Total",
    "NCC Junior: SICU Total",
    "NCC Senior: Elective Total",
    "NCC Junior: Elective Total",
    "NCC Senior: Vacation Total",
    "NCC Junior: Vacation Total",
    "NH: Vacation Total",
    "Stroke: Elective Total",
    "NH Elective Budget",
    "Stroke: No NCC in First Block",
]

# Rules that are active:false in the live config but the user wants tried by
# activating them first (as soft). Hardening these requires --activate too.
INACTIVE_SOFT = {
    "Swing Coverage Target",
    "Half-Year Balance",
    "Stroke Short Core ICU Runs",
}


def _iter_rule_dicts(annual):
    """Yield every rule dict across the standing and annual rule lists."""
    yield from annual.get("standing_rules", [])
    yield from annual.get("rules", [])


def _find_rules(annual, name):
    return [r for r in _iter_rule_dicts(annual) if r.get("name") == name]


def build_config_with_overrides(
    *, hardened=frozenset(), activated=frozenset(), band_overrides=None,
    no_lock=False, verbose=False,
):
    """Assemble fresh config dicts, apply overrides, build a ScheduleSolverConfig.

    hardened:  rule names to flip strength soft -> hard.
    activated: rule names to flip active -> True.
    band_overrides: {name: (lo, hi)} — replace the matched rule with a hard
                    at_least lo + at_most hi pair (the zero-code weaker band).
    no_lock:   build with locked_assignments cleared (for the false-UNSAT A/B).
    """
    annual = wb2.assemble_config_dicts(verbose=verbose)
    band_overrides = band_overrides or {}

    for name in activated:
        for r in _find_rules(annual, name):
            r["active"] = True

    for name in hardened:
        matched = _find_rules(annual, name)
        if not matched:
            raise KeyError(f"hardened rule not found: {name!r}")
        for r in matched:
            r["strength"] = "hard"

    for name, (lo, hi) in band_overrides.items():
        _apply_band(annual, name, lo, hi)

    if no_lock:
        annual["locked_assignments"] = {}

    config = build_solver_config_from_request(annual, standing_path=STANDING)
    return config


def _apply_band(annual, name, lo, hi):
    """Replace rule *name* with a hard at_least lo + at_most hi pair, in place."""
    for lst_key in ("standing_rules", "rules"):
        lst = annual.get(lst_key, [])
        for i, r in enumerate(lst):
            if r.get("name") != name:
                continue
            base = copy.deepcopy(r)
            base["active"] = True
            lo_rule = copy.deepcopy(base)
            lo_rule["strength"] = "hard"
            lo_rule["relation"] = "at_least"
            lo_rule["count"] = lo
            lo_rule["name"] = f"{name} (band >= {lo})"
            hi_rule = copy.deepcopy(base)
            hi_rule["strength"] = "hard"
            hi_rule["relation"] = "at_most"
            hi_rule["count"] = hi
            hi_rule["name"] = f"{name} (band <= {hi})"
            lst[i:i + 1] = [lo_rule, hi_rule]
            return
    raise KeyError(f"band rule not found: {name!r}")


@dataclass
class CheckResult:
    state: str            # "SAT" | "UNSAT" | "UNKNOWN"
    seconds: float
    total: int | None = None
    weekly: int | None = None
    call: int | None = None
    optimal: bool = False


def sat_check(config, runner, *, sat_limit=20.0, unsat_limit=150.0):
    """Two-tier SAT check. Confirm SAT quickly; only if UNKNOWN, spend the
    longer budget trying to prove UNSAT."""
    opb, var_map = build_full_schedule_opb(config, objective=True)
    t0 = time.time()
    res = runner.optimize(opb, time_limit=sat_limit)
    if res.satisfiable and res.assignment is not None:
        wk, cn, tot = soft_penalty_breakdown(res.assignment, var_map)
        return CheckResult("SAT", time.time() - t0, tot, wk, cn, res.optimal)
    if res.proven_unsat:
        return CheckResult("UNSAT", time.time() - t0)
    # UNKNOWN after the short pass — spend the long budget to prove UNSAT.
    res = runner.optimize(opb, time_limit=unsat_limit)
    if res.satisfiable and res.assignment is not None:
        wk, cn, tot = soft_penalty_breakdown(res.assignment, var_map)
        return CheckResult("SAT", time.time() - t0, tot, wk, cn, res.optimal)
    if res.proven_unsat:
        return CheckResult("UNSAT", time.time() - t0)
    return CheckResult("UNKNOWN", time.time() - t0)


def _fmt(r: CheckResult):
    pen = "" if r.total is None else f"{r.total}({r.weekly}/{r.call})"
    opt = "opt" if r.optimal else ""
    return f"{r.state:8s} {r.seconds:6.1f}s  {pen:18s} {opt}"


def _print_row(label, kind, change, r: CheckResult, note=""):
    print(f"  {label:42s} {kind:18s} {change:10s} {_fmt(r)}  {note}", flush=True)


def run_phase_a(order, runner, *, sat_limit, unsat_limit, activated, no_lock):
    print("\n=== Phase A: per-rule isolation ===", flush=True)
    print(f"  {'rule':42s} {'type':18s} {'change':10s} state    secs    penalty(tot(wk/call)) note", flush=True)
    results = {}
    # Baseline: no hardenings.
    cfg = build_config_with_overrides(activated=activated, no_lock=no_lock)
    base = sat_check(cfg, runner, sat_limit=sat_limit, unsat_limit=unsat_limit)
    _print_row("(baseline, no hardening)", "-", "-", base)
    results["__baseline__"] = asdict(base)
    for name in order:
        try:
            cfg = build_config_with_overrides(
                hardened={name}, activated=activated, no_lock=no_lock)
        except KeyError as e:
            _print_row(name, "?", "harden", CheckResult("SKIP", 0.0), note=str(e))
            continue
        r = sat_check(cfg, runner, sat_limit=sat_limit, unsat_limit=unsat_limit)
        _print_row(name, "", "harden", r)
        results[name] = asdict(r)
    return results


def run_phase_b(order, runner, *, sat_limit, unsat_limit, activated, no_lock,
                stop_on_first_unsat):
    print("\n=== Phase B: cumulative ===", flush=True)
    print(f"  {'rule (added)':42s} {'type':18s} {'change':10s} state    secs    penalty(tot(wk/call)) note", flush=True)
    results = {}
    accepted = set()
    cfg = build_config_with_overrides(activated=activated, no_lock=no_lock)
    base = sat_check(cfg, runner, sat_limit=sat_limit, unsat_limit=unsat_limit)
    _print_row("(baseline, no hardening)", "-", "-", base)
    results["__baseline__"] = asdict(base)
    for name in order:
        trial = accepted | {name}
        try:
            cfg = build_config_with_overrides(
                hardened=trial, activated=activated, no_lock=no_lock)
        except KeyError as e:
            _print_row(name, "?", "+harden", CheckResult("SKIP", 0.0), note=str(e))
            continue
        r = sat_check(cfg, runner, sat_limit=sat_limit, unsat_limit=unsat_limit)
        if r.state == "SAT":
            accepted.add(name)
            note = f"accepted ({len(accepted)} hard)"
        else:
            note = "REJECTED (kept soft)"
        _print_row(name, "", "+harden", r, note=note)
        results[name] = {**asdict(r), "accepted": r.state == "SAT"}
        if r.state == "UNSAT" and stop_on_first_unsat:
            print("  (stopping on first UNSAT)", flush=True)
            break
    print(f"\n  Cumulatively hardenable: {sorted(accepted)}", flush=True)
    return results, sorted(accepted)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--phase", choices=["a", "b", "both"], default="both")
    ap.add_argument("--sat-limit", type=float, default=20.0)
    ap.add_argument("--unsat-limit", type=float, default=150.0)
    ap.add_argument("--order", type=str, default=None,
                    help="File with one rule name per line; overrides default order.")
    ap.add_argument("--only", type=str, default=None,
                    help="Comma-separated rule names to test in isolation (forces phase a).")
    ap.add_argument("--no-lock", action="store_true",
                    help="Build with locked_assignments cleared (false-UNSAT A/B).")
    ap.add_argument("--activate", type=str, default=None,
                    help="Comma-separated inactive rule names to activate (as soft) first.")
    ap.add_argument("--stop-on-first-unsat", action="store_true")
    ap.add_argument("--json", type=str, default=None, help="Write results JSON here.")
    args = ap.parse_args()

    if args.order:
        with open(args.order) as fh:
            order = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    elif args.only:
        order = [s.strip() for s in args.only.split(",") if s.strip()]
    else:
        order = list(DEFAULT_ORDER)

    activated = frozenset(s.strip() for s in (args.activate or "").split(",") if s.strip())

    runner = RoundingSatRunner(ROUNDINGSAT)
    print(f"RoundingSat: {ROUNDINGSAT}", flush=True)
    print(f"Budgets: SAT={args.sat_limit}s, UNSAT={args.unsat_limit}s | "
          f"no_lock={args.no_lock} | activate={sorted(activated)}", flush=True)

    out = {"args": vars(args), "order": order}
    phase = "a" if args.only else args.phase
    if phase in ("a", "both"):
        out["phase_a"] = run_phase_a(
            order, runner, sat_limit=args.sat_limit, unsat_limit=args.unsat_limit,
            activated=activated, no_lock=args.no_lock)
    if phase in ("b", "both"):
        out["phase_b"], out["cumulative_hardenable"] = run_phase_b(
            order, runner, sat_limit=args.sat_limit, unsat_limit=args.unsat_limit,
            activated=activated, no_lock=args.no_lock,
            stop_on_first_unsat=args.stop_on_first_unsat)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=2)
        print(f"\nWrote {args.json}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
