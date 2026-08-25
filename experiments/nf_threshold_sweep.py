"""Threshold sweep for NF background-rotation quotas.

Rebuilds the assembled NF config's rotation constraints from a compact spec
(per-group, per-shift {relation, count, strength}) WITHOUT editing YAML, then
classifies each config as SAT(fast) / UNSAT / TIMEOUT. Used to map the feasibility
boundary: how close to the ideal targets can we get without forcing UNSAT or
intractable solves, and which service is binding.

Usage: PYTHONPATH=src .venv/bin/python experiments/nf_threshold_sweep.py
"""
from __future__ import annotations

import dataclasses
import subprocess
import time
from pathlib import Path

from parafrost_scheduler.experiment import assemble_config
from parafrost_scheduler.schedule_encoder import build_full_schedule_opb
from parafrost_scheduler.roundingsat_runner import RoundingSatRunner
from scheduler.semantic_constraints import (
    SemanticConstraint, ConstraintLifecycle, ConstraintStrength, FellowSelector, ShiftSet, WeekSpan)

_REPO = Path(__file__).resolve().parent.parent
_RUNNER = RoundingSatRunner(_REPO / "vendor/roundingsat/build/roundingsat")
_ANNUAL = _REPO / "config/annual/ncc-nf-model.yaml"
_STANDING = _REPO / "config/standing/ncc-nf-model.yaml"

_ROT = ("shift_total", "all_or_none_block")
_H = ConstraintStrength.HARD
_S = ConstraintStrength.SOFT


def _base_cfg():
    res = assemble_config(None, annual_path=_ANNUAL, standing_path=_STANDING, verbose=False)
    return res[0] if isinstance(res, tuple) else res


def _st(group, shift, relation, count, strength, window=None, name=None):
    return SemanticConstraint(
        kind="shift_total", lifecycle=ConstraintLifecycle.ANNUAL_RULE, strength=strength,
        fellows=FellowSelector.by_groups(group),
        weeks=WeekSpan(window[0], window[1]) if window else None,
        shifts=ShiftSet(name or f"{group} {shift}", (shift,)),
        params={"name": name or f"{group} {shift} {relation} {count}",
                "relation": relation, "count": count})


def _block(group, shift, size, strength=_H):
    return SemanticConstraint(
        kind="all_or_none_block", lifecycle=ConstraintLifecycle.ANNUAL_RULE, strength=strength,
        fellows=FellowSelector.by_groups(group),
        shifts=ShiftSet(f"{group} {shift} block", (shift,)),
        params={"name": f"{group} {shift} {size}wk block", "block_size": size})


def classify(rotation_rules, timeout=45.0):
    """Build the NF model with these rotation rules (replacing the config's own)
    and return (verdict, seconds)."""
    cfg = _base_cfg()
    keep = [c for c in cfg.constraints if c.kind not in _ROT]
    cfg = dataclasses.replace(cfg, constraints=keep + list(rotation_rules))
    opb, _ = build_full_schedule_opb(cfg, objective=False)
    t = time.time()
    try:
        r = _RUNNER.solve(opb, timeout=timeout)
        return ("SAT" if r.satisfiable else "UNSAT", round(time.time() - t, 1))
    except subprocess.TimeoutExpired:
        return ("TIMEOUT", timeout)


# Rotation rule SETS, parameterized so a sweep can vary one threshold at a time.
def jr_rules(*, micu=16, elec_relation="exactly", elec=12, sicu=4, anaes=4, vac=3,
            elec_strength=_S):
    return [
        _st("NCC_JR", "MICU", "exactly", micu, _H),
        _st("NCC_JR", "MICU", "exactly", 4, _H, window=(0, 4), name="JR MICU orientation"),
        _block("NCC_JR", "Anaesthesia", 4),
        _st("NCC_JR", "Anaesthesia", "exactly", anaes, _H),
        _st("NCC_JR", "SICU", "exactly", sicu, _H),
        _st("NCC_JR", "Vac", "exactly", vac, _H),
        _st("NCC_JR", "Elec", elec_relation, elec, elec_strength, name="JR Elective"),
    ]


def sr_rules(*, micu=8, ns=6, stroke=2, tele=2, vac=3, elec_relation="exactly", elec=9,
            elec_strength=_S):
    return [
        _st("NCC_SR", "MICU", "exactly", micu, _H),
        _block("NCC_SR", "NS", 2),
        _st("NCC_SR", "NS", "exactly", ns, _H),
        _st("NCC_SR", "Stroke", "exactly", stroke, _H),
        _st("NCC_SR", "Telestroke/Clinic", "exactly", tele, _H),
        _st("NCC_SR", "Vac", "exactly", vac, _H),
        _st("NCC_SR", "Elec", elec_relation, elec, elec_strength, name="SR Elective"),
    ]


def run(label, rules, timeout=45.0):
    v, s = classify(rules, timeout)
    print(f"{label:52s} -> {v:8s} {s}s", flush=True)
    return v, s


if __name__ == "__main__":
    print("=== baseline: current shipped quotas (SR Elec soft) ===")
    run("current (JR elec soft 12, SR elec soft 9)", jr_rules() + sr_rules())

    print("\n=== Q1: hard exactly Elec vs hard at_least floor ===")
    run("JR+SR, SR Elec HARD exactly 9", jr_rules() + sr_rules(elec_strength=_H))
    run("JR+SR, SR Elec HARD at_least 8", jr_rules() + sr_rules(elec_relation="at_least", elec=8, elec_strength=_H))

    print("\n=== Q2: the IDEAL targets (JR elec 13, SR elec 11), all hard ===")
    run("IDEAL all-hard (JR elec13, SR elec11)",
        jr_rules(elec=13, elec_strength=_H) + sr_rules(elec=11, elec_strength=_H))
    run("IDEAL, Elec soft (JR13/SR11 soft)",
        jr_rules(elec=13, elec_strength=_S) + sr_rules(elec=11, elec_strength=_S))
