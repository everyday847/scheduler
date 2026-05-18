from typing import List, Dict

from z3 import *
import openpyxl
from pathlib import Path
import yaml

import tqdm

try:
    from .date_to_week_index import date_to_week_index
    from .constraints import ScheduleConstraints
    from .fellow_mapping import FellowMapping
    from .rule_application import RuleApplicationContext, apply_constraints
    from .semantic_constraints import ConstraintStrength
    from .standing_rules import constraints_from_config as standing_constraints_from_config
except ImportError:  # pragma: no cover - supports running from src/scheduler
    from date_to_week_index import date_to_week_index
    from constraints import ScheduleConstraints
    from fellow_mapping import FellowMapping
    from rule_application import RuleApplicationContext, apply_constraints
    from semantic_constraints import ConstraintStrength
    from standing_rules import constraints_from_config as standing_constraints_from_config

z3.set_option(verbose=0)
# Enable parallel solving globally
set_param('tactic.default_tactic', 'smt')
set_param('parallel.enable', True)
set_param('verbose', 10)

# Not too dangerous to make global
W = 52
STANDING_RULE_CONFIG = Path(__file__).resolve().parents[2] / "config" / "standing" / "stanford-fellowship.yaml"

def range_fellows_assigned_fully(o, x, R, fellow_indices):
    # Each NCC fellow has exactly one rotation per week, because we are responsible for their schedule.
    for f in fellow_indices:
        for w in range(W):
            o.add(AtLeast(*[x[f, w, r] for r in R], 1))


def everyone_one_rotation_per_week(o, x, R, fellow_indices):
    # Each other fellow has at most one rotation per week, since we are only assigning their NCC time.
    for f in fellow_indices:
        for w in range(W):
            o.add(AtMost(*[x[f, w, r] for r in R], 1))


def ncc_shifts_covered_swing_deficit(o, x, total_fellows, deficit):
    # There is one fellow on Swing and at least one fellow on NCC1, NCC2 per week.
    # We can be more specific if this gets nuts with overassignment.
    for w in range(W):
        o.add(Sum([If(x[f, w, "NCC1"], 1, 0) for f in range(total_fellows)]) >= 1)
        o.add(Sum([If(x[f, w, "NCC2"], 1, 0) for f in range(total_fellows)]) >= 1)
        o.add(Sum([If(x[f, w, "NCC1"], 1, 0) for f in range(total_fellows)]) <= 2)
        o.add(Sum([If(x[f, w, "NCC2"], 1, 0) for f in range(total_fellows)]) <= 2)
        
        # At most one extra fellow on at once.
        o.add(Sum([If(Or(x[f, w, "NCC1"], x[f, w, "NCC2"]), 1, 0) for f in range(total_fellows)]) <= 3)

        # Covering swing as well
        o.add(Sum([If(Or(x[f, w, "NCC1"], x[f, w, "NCC2"], x[f, w, "Swing"]), 1, 0) for f in range(total_fellows)]) <= 4)

        # LE because inadequacy
        o.add(Sum([If(x[f, w, "Swing"], 1, 0) for f in range(total_fellows)]) <= 1)

    # Ah, we might not actually have enough swing. Let's say at most 8 weeks are
    # unassigned.
    o.add(Sum([
        Sum([If(x[f, w, "Swing"], 1, 0) for f in range(total_fellows)])
        for w in range(W)]) >= W-deficit)

def stroke_shifts_covered(o, x, total_fellows, fellow_mapping):
    # Every week has exactly one person on Stroke and on Telestroke/Clinic
    for w in range(W):
        stroke_coverage = Sum([If(x[f, w, "Stroke"], 1, 0) for f in range(total_fellows)]) == 1
        o.add(stroke_coverage)
        o.add(Sum([If(x[f, w, "Telestroke/Clinic"], 1, 0) for f in range(total_fellows)]) == 1)

def ncc_stroke_oversight(o, x, fellow_indices):
    # IDEALLY every week either NCC1 or NCC2 is neurocrit or stroke.
    for w in range(W):
        o.add(
            Sum([
                If(
                    Or(x[f, w, "NCC1"], x[f, w, "NCC2"]),
                    1,
                    0
                ) for f in fellow_indices
            ]) >= 1
        )

def maximum_consecutive_icu_shifts(o, x, fellow_indices, shifts, MAX_CONSEC):
    for f in fellow_indices:
        for w in range(W - MAX_CONSEC):
            o.add(Sum([If(
                Or(*[x[f, w + i, r] for r in shifts]),
                1,
                0) for i in range(MAX_CONSEC + 1)]) <= MAX_CONSEC)

def maximum_consecutive_icu_shifts_soft(o, x, fellow_indices, shifts, MAX_CONSEC):
    for f in fellow_indices:
        for w in range(W - MAX_CONSEC):
            o.add_soft(Sum([If(
                Or(*[x[f, w + i, r] for r in shifts]),
                1,
                0) for i in range(MAX_CONSEC + 1)]) <= MAX_CONSEC)

def jr_first_month_micu(o, x, fellow_indices):
    # jr fellows first month is MICU
    for f in fellow_indices:
        for w in range(4):
            o.add(x[f, w, "MICU"])

def jr_ncc_before_19(o, x, fellow_indices):
    # jr fellows have a block of NCC before week 19
    for f in fellow_indices:
        o.add(
            Sum([If(Or(x[f, w, "NCC1"], x[f, w, "NCC2"]), 1, 0) for w in range(4, 19)]) >= 1
        )

def ccm_total_service(o, x, fellow_indices):
    # ccm fellows have precisely one month of NCC, of which one week is swing
    # TODO: follow 'block' boundaries
    for f in fellow_indices:
        zero_shift_service(o, x, f, "NS")
        # Well, they do plenty of this, but we're not in charge of it
        zero_shift_service(o, x, f, "MICU")
        zero_shift_service(o, x, f, "SICU")
        zero_shift_service(o, x, f, "Anaesthesia")
        zero_shift_service(o, x, f, "Stroke") # 11 or 12 in a 53
        zero_shift_service(o, x, f, "Clinic/Elective")
        zero_shift_service(o, x, f, "Telestroke/Clinic")
        zero_shift_service(o, x, f, "Elec")
        zero_shift_service(o, x, f, "SCVMC Rehab")
        zero_shift_service(o, x, f, "NIR")
        zero_shift_service(o, x, f, "Vac")
        zero_shift_service(o, x, f, "ISC")

        # Total for the year
        o.add(Sum([If(Or(x[f, w, "NCC1"], x[f, w, "NCC2"]), 1, 0) for w in range(52)]) == 3)
        o.add(Sum([If(x[f, w, "Swing"], 1, 0) for w in range(52)]) == 1)

        # Consecutivity
        for w in range(0, W, 4):
            # Either all or none of the block is NCC-ish.
            o.add(
                If(
                    Sum([If(Or(x[f, w_, "NCC1"], x[f, w_, "NCC2"], x[f, w_, "Swing"]), 1, 0) for w_ in range(w, w + 4)]) > 0,
                    Sum([If(Or(x[f, w_, "NCC1"], x[f, w_, "NCC2"], x[f, w_, "Swing"]), 1, 0) for w_ in
                         range(w, w + 4)]),
                    4) == 4 # if the whole block isn't NCCish, this doesn't fail condition
            )
            o.add(
                If(
                    Sum([If(Or(x[f, w_, "NCC1"], x[f, w_, "NCC2"], x[f, w_, "Swing"]), 1, 0) for w_ in
                         range(w, w + 4)]) > 0,
                    Sum([If(x[f, w_, "Swing"], 1, 0) for w_ in
                         range(w, w + 4)]),
                    1) == 1  # if the whole block isn't NCCish, this doesn't fail condition
            )

def total_shift_service(o, x, f, shift, n):
    o.add(Sum([If(x[f, w, shift], 1, 0) for w in range(W)]) >= n)

def total_shift_service_exact(o, x, f, shift, n):
    o.add(Sum([If(x[f, w, shift], 1, 0) for w in range(W)]) == n)

def zero_shift_service(o, x, f, shift):
    for w in range(W):
        o.add(Not(x[f, w, shift]))

def total_nicu_service(o, x, f, n):
    o.add(Sum([If(Or(x[f, w, "NCC1"], x[f, w, "NCC2"]), 1, 0) for w in range(W)]) >= n)

def total_nicu_service_exact(o, x, f, n):
    o.add(Sum([If(Or(x[f, w, "NCC1"], x[f, w, "NCC2"]), 1, 0) for w in range(W)]) == n)

def stroke_total_service(o, x, fellow_indices):
    for f in fellow_indices:
        if True:
            total_shift_service_exact(o, x, f, "Swing", 2)
            total_nicu_service_exact(o, x, f, 4) # *non-swing* NICU service!!
        if True:
            total_shift_service(o, x, f, "Stroke", 11) # 11 or 12 in a 53
            total_shift_service(o, x, f, "Elec", 4)
            total_shift_service(o, x, f, "Vac", 3)
            total_shift_service(o, x, f, "Telestroke/Clinic", 11)
        # NCC ONLY
        zero_shift_service(o, x, f, "NS")
        zero_shift_service(o, x, f, "MICU")
        zero_shift_service(o, x, f, "SICU")
        zero_shift_service(o, x, f, "Anaesthesia")
        if True:
            total_shift_service(o, x, f, "Clinic/Elective", 11)
            total_shift_service_exact(o, x, f, "SCVMC Rehab", 2)
            total_shift_service_exact(o, x, f, "NIR", 2)
            total_shift_service_exact(o, x, f, "ISC", 1)

def ncc_jr_total_service(o, x, fellow_indices):
    for f in fellow_indices:
        total_shift_service(o, x, f, "MICU", 20)
        total_shift_service(o, x, f, "Anaesthesia", 4)
        total_shift_service(o, x, f, "Elec", 9)
        total_shift_service(o, x, f, "Vac", 3)
        zero_shift_service(o, x, f, "NS")
        total_shift_service(o, x, f, "SICU", 4)
        zero_shift_service(o, x, f, "Stroke")
        zero_shift_service(o, x, f, "Telestroke/Clinic")
        total_shift_service(o, x, f, "Swing", 3) # not sure how this was 6 TODO
        total_nicu_service_exact(o, x, f, 9)
        # Stroke-only shifts
        zero_shift_service(o, x, f, "SCVMC Rehab")
        zero_shift_service(o, x, f, "NIR")
        zero_shift_service(o, x, f, "Clinic/Elective")
        zero_shift_service(o, x, f, "ISC")

def ncc_sr_total_service(o, x, fellow_indices):
    for f in fellow_indices:
        total_shift_service(o, x, f, "MICU", 8)
        zero_shift_service(o, x, f, "Anaesthesia")
        total_shift_service(o, x, f, "Elec", 10)
        total_shift_service(o, x, f, "Vac", 3)
        total_shift_service(o, x, f, "NS", 7)
        zero_shift_service(o, x, f, "SICU")
        total_shift_service(o, x, f, "Stroke", 2)
        total_shift_service(o, x, f, "Telestroke/Clinic", 2)
        total_shift_service(o, x, f, "Swing", 6)
        total_nicu_service_exact(o, x, f, 14)
        zero_shift_service(o, x, f, "SCVMC Rehab")
        zero_shift_service(o, x, f, "NIR")
        zero_shift_service(o, x, f, "Clinic/Elective")
        zero_shift_service(o, x, f, "ISC")

def nh_total_service(o, x, fellow_indices):
    for f in fellow_indices:
        total_nicu_service_exact(o, x, f, 4)
        total_shift_service_exact(o, x, f, "Swing", 1)
        total_shift_service_exact(o, x, f, "Telestroke/Clinic", 3)
        total_shift_service_exact(o, x, f, "Stroke", 4)

        zero_shift_service(o, x, f, "MICU")
        zero_shift_service(o, x, f, "Anaesthesia")
        zero_shift_service(o, x, f, "Elec")
        zero_shift_service(o, x, f, "Vac")
        zero_shift_service(o, x, f, "NS")
        zero_shift_service(o, x, f, "SICU")

        zero_shift_service(o, x, f, "SCVMC Rehab")
        zero_shift_service(o, x, f, "NIR")
        zero_shift_service(o, x, f, "Clinic/Elective")
        zero_shift_service(o, x, f, "ISC")


def jr_fellows_n_ncc_before_swing(o, x, fellow_indices, n):
    # jr fellows have 4x NCC before their first swing
    for f in fellow_indices:
        o.add(
            Sum([
                Product([
                    # Number of NCC shifts before week w.
                    Sum([
                        If(
                            Or(x[f, w_, "NCC1"], x[f, w_, "NCC2"]),
                            1,
                            0
                        )
                    for w_ in range(w)]),
                    # Zero if there is a swing shift before week w, or if week w itself is not a swing shift.
                    If(
                        And(
                            Sum([
                                If(
                                    x[f, w_, "Swing"],
                                    1,
                                    0
                                ) for w_ in range(w)
                            ]) == 0,
                            x[f,w,"Swing"],
                        ),
                        1,
                        0
                    )
                ])
            for w in range(W)]) >= n
        )

def nh_first_stroke_with_victoria(o, x, f, fprime):
    o.add(
        Sum([
            Product([
                # 1 for the fellow's first stroke shift, 0 otherwise.
                Product([
                    # Number of Stroke shifts before week w.
                    Sum([
                        If(
                            x[f, w_, "Stroke"],
                            1,
                            0
                        )
                        for w_ in range(w)]),
                    # Zero if there is a Stroke shift before week w, or if week w itself is not a Stroke shift.
                    If(
                        And(
                            Sum([
                                If(
                                    x[f, w_, "Stroke"],
                                    1,
                                    0
                                ) for w_ in range(w)
                            ]) == 0,
                            x[f, w, "Stroke"],
                        ),
                        1,
                        0
                    )
                ]),
                If(x[fprime, w, "Stroke"], 1, 0)
            ])
            for w in range(W)
        ]) == 1
    )

def shift_blocked(o, x, shift, fellow_indices, GRANULARITY):
    # junior fellows have 4 sicu, and it should follow a block.
    for f in fellow_indices:
        # Consecutivity
        for w in range(0, W, GRANULARITY):
            # Either all or none of the block is SICU.
            o.add(
                Or(
                    Sum([
                        If(x[f, w_, shift], 1, 0) for w_ in range(w, w + GRANULARITY)
                    ]) == GRANULARITY,
                    Sum([
                        If(x[f, w_, shift], 1, 0) for w_ in range(w, w + GRANULARITY)
                    ]) == 0,
                )
            )

def shift_blocked_soft(o, x, shift, fellow_indices, GRANULARITY):
    for f in fellow_indices:
        # Consecutivity
        for w in range(0, W, GRANULARITY):
            # Either all or none of the block is SICU.
            o.add_soft(
                Or(
                    Sum([
                        If(x[f, w_, shift], 1, 0) for w_ in range(w, w + GRANULARITY)
                    ]) == GRANULARITY,
                    Sum([
                        If(x[f, w_, shift], 1, 0) for w_ in range(w, w + GRANULARITY)
                    ]) == 0,
                )
            )

def sicu_blocked(o, x, fellow_indices):
    # junior fellows have 4 sicu, and it should follow a block.
    shift_blocked(o, x, "SICU", fellow_indices, GRANULARITY=4)

def micu_blocked(o, x, fellow_indices):
    # jr and sr fellows have lots of micu, and it should follow a block.
    shift_blocked(o, x, "MICU", fellow_indices, GRANULARITY=4)

def anaesthesia_blocked(o, x, fellow_indices):
    # jr and sr fellows have lots of anaesthesia, and it should follow a block.
    shift_blocked(o, x, "Anaesthesia", fellow_indices, GRANULARITY=4)

def scvmc_blocked(o, x, fellow_indices):
    # jr and sr fellows have lots of scvmc, and it should follow a block.
    shift_blocked(o, x, "SCVMC Rehab", fellow_indices, GRANULARITY=2)

def vasc_blocked(o, x, fellow_indices):
    # jr and sr fellows have lots of micu, and it should follow a block.
    shift_blocked(o, x, "Stroke", fellow_indices, GRANULARITY=2)
    shift_blocked(o, x, "Telestroke/Clinic", fellow_indices, GRANULARITY=2)

def ns_blocked(o, x, fellow_indices):
    # jr and sr fellows have lots of micu, and it should follow a block.
    GRANULARITY = 4
    shift = "NS"

    for f in fellow_indices:
        # Consecutivity
        for w in range(0, W, GRANULARITY):
            # Either all or none of the block is SICU.
            o.add(
                Or(
                    Sum([
                        If(x[f, w_, shift], 1, 0) for w_ in range(w, w + GRANULARITY)
                    ]) == GRANULARITY,
                    Sum([
                        If(x[f, w_, shift], 1, 0) for w_ in range(w, w + 3)
                    ]) == 3,
                    Sum([
                        If(x[f, w_, shift], 1, 0) for w_ in range(w, w + GRANULARITY)
                    ]) == 0,
                )
            )

def ncc_blocked(o, x, fellow_indices):
    # TODO: for now we are requiring 2 block
    GRANULARITY = 2
    for f in fellow_indices:
        # Consecutivity
        for w in range(0, W, GRANULARITY):
            o.add(
                Or(
                    Sum([
                        If(Or(x[f, w_, "NCC1"], x[f, w_, "Swing"]), 1, 0) for w_ in range(w, w + GRANULARITY)
                    ]) == GRANULARITY,
                    Sum([
                        If(Or(x[f, w_, "NCC2"], x[f, w_, "Swing"]), 1, 0) for w_ in range(w, w + GRANULARITY)
                    ]) == GRANULARITY,
                    Sum([
                        If(Or(x[f, w_, "NCC1"], x[f, w_, "NCC2"], x[f, w_, "Swing"]), 1, 0) for w_ in range(w, w + GRANULARITY)
                    ]) == 0,
                )
            )

    GRANULARITY2 = 4
    for f in fellow_indices:
        # Consecutivity
        for w in range(0, W, GRANULARITY2):
            o.add_soft(
                Or(
                    Sum([
                        If(Or(x[f, w_, "NCC1"], x[f, w_, "Swing"]), 1, 0) for w_ in
                        range(w, w + GRANULARITY2)
                    ]) == GRANULARITY2,
                    Sum([
                        If(Or(x[f, w_, "NCC2"], x[f, w_, "Swing"]), 1, 0) for w_ in
                        range(w, w + GRANULARITY2)
                    ]) == GRANULARITY2,
                    Sum([
                        If(Or(x[f, w_, "NCC1"], x[f, w_, "NCC2"], x[f, w_, "Swing"]), 1, 0) for w_ in
                        range(w, w + GRANULARITY2)
                    ]) == 0,
                )
            )

def vacation_requests(o, x, fellow_mapping, fellow_week_pairs, n_vac):
    # prash wants weeks 1, 7, and 36
    # (figure out a way to express this TODO)
    for fellow_name, weeks in fellow_week_pairs.items():
        f = fellow_mapping.get_fellow_index(fellow_name)
        if f is not None:
            for w in weeks[:n_vac]:
                o.add(x[f, w, "Vac"])
            for w in weeks[n_vac:]:
                o.add_soft(x[f, w, "Elec"])

def fourth_block_two_micu_fellows(o, x, fellow_indices):
    # from the fellows between start and end, ensure MICU is double-staffed for every week from 12-15
    for w in range(12,16):
        o.add(
            Sum([If(x[f, w, "MICU"], 1, 0) for f in fellow_indices]) == 2,
        )

def nir_one_week_per_half(o, x, fellow_indices):
    for f in fellow_indices:
        o.add(
            Sum([
                If(x[f, w, "NIR"], 1, 0) for w in range(0, W // 2)
            ]) == 1
        )
        o.add(
            Sum([
                If(x[f, w, "NIR"], 1, 0) for w in range(W // 2, W)
            ]) == 1
        )

def scvmc_second_half(o, x, fellow_indices):
    for f in fellow_indices:
        o.add(
            Sum([
                If(x[f, w, "SCVMC Rehab"], 1, 0) for w in range(W // 2, W)
            ]) == 2
        )

def comparable_amounts_each_half_year(o, x, fellow_indices):
    # I don't want any shift to be massively frontloaded or backloaded.
    # no one's year should end with 8 NCC, 2 Elec, 8 NCC, 2 Elec, 8 NCC
    for f in fellow_indices:
        o.add(
            Abs(
                Sum([
                    If(x[f, w, "MICU"], 1, 0) for w in range(0, W // 2)
                ]) -
                Sum([
                    If(x[f, w, "MICU"], 1, 0) for w in range(W // 2, W)
                ])
            ) <= 4
        )

        o.add(
            Abs(
                Sum([
                    If(Or(x[f, w, "NCC1"], x[f, w, "NCC2"], x[f, w, "Swing"]), 1, 0) for w in range(0, W // 2)
                ]) -
                Sum([
                    If(Or(x[f, w, "NCC1"], x[f, w, "NCC2"], x[f, w, "Swing"]), 1, 0) for w in range(W // 2, W)
                ])
            ) <= 4
        )

def specific_assignment(o, x, shift, fellow_indices, week):
    o.add(Or(*[x[f, week, shift] for f in fellow_indices]))

def specific_assignment_soft(o, x, shift, fellow_indices, week):
    o.add_soft(Or(*[x[f, week, shift] for f in fellow_indices]))

def first_thirteen_ccm_alphabetical(o, x, fellow_indices):
    """
    The first CCM fellow works the first block and not the others.
    """
    for f, w in zip(fellow_indices[:13], range(0, W, 4)):
        # f works only in w, w+1, w+2, w+3
        for w_ in range(W):
            # The only thing they do is NCC1/2/swing, so let's just focus there
            for s in ["NCC1", "NCC2", "Swing"]:
                if w_ < w or w_ > w+3:
                    o.add(Not(x[f, w_, s]))

def lia_thing(o, x, fellow_indices, shifts):
    """
    Lia specifically does 3 shifts of NCC, and nothing after 9/30.
    """
    for f in fellow_indices:
        # take on some of these pls
        for w in range(4):
            o.add(
                Or([
                    x[f, w, "NCC1"], x[f, w, "NCC2"], x[f, w, "Swing"]
                ])
            )

            for s in shifts:
                if s in ["NCC1", "NCC2", "Swing"]: continue
                o.add(
                    Not(x[f, w, s])
                )

        for w in range(4, W):
            for s in shifts:
                o.add(
                    Not(x[f, w, s])
                )

        o.add_soft(
            Sum([
                If(x[f, w, "Swing"], 1, 0) for w in range(14)
            ]) <= 1
        )

def stroke_no_block_one_ncc(o, x, fellow_indices):
    for f in fellow_indices:
        for w in range(4):
            o.add_soft(Not(x[f, w, "NCC1"]))
            o.add_soft(Not(x[f, w, "NCC2"]))
            o.add(Not(x[f, w, "Swing"]))

def isc(o, x, fellow_indices):
    for f in fellow_indices:
        for w in range(52):
            w_ = date_to_week_index((2026, 2, 5))
            if w == w_:
                o.add(x[f, w, "ISC"])
            else:
                o.add(Not(x[f, w, "ISC"]))

def existing_fellow_indices(fellow_mapping, names):
    return [
        fellow_mapping.get_fellow_index(name)
        for name in names
        if fellow_mapping.get_fellow(name) is not None
    ]

def _rule_handlers():
    return {
        "all_or_none_block": _apply_all_or_none_block,
        "ccm_total_service": _apply_ccm_total_service,
        "comparable_half_year_distribution": _apply_comparable_half_year_distribution,
        "full_assignment": _apply_full_assignment,
        "fourth_block_two_micu_fellows": _apply_fourth_block_two_micu_fellows,
        "isc": _apply_isc,
        "jr_first_month_micu": _apply_jr_first_month_micu,
        "jr_ncc_before_swing": _apply_jr_ncc_before_swing,
        "jr_ncc_before_week": _apply_jr_ncc_before_week,
        "max_consecutive": _apply_max_consecutive,
        "minimize_uncovered_shift_weeks": _apply_minimize_uncovered_shift_weeks,
        "ncc_coverage": _apply_ncc_coverage,
        "ncc_stroke_oversight": _apply_ncc_stroke_oversight,
        "nh_total_service": _apply_nh_total_service,
        "ncc_jr_total_service": _apply_ncc_jr_total_service,
        "ncc_sr_total_service": _apply_ncc_sr_total_service,
        "nir_one_week_per_half": _apply_nir_one_week_per_half,
        "scvmc_second_half": _apply_scvmc_second_half,
        "specific_assignment": _apply_specific_assignment,
        "stroke_shift_coverage": _apply_stroke_shift_coverage,
        "stroke_no_block_one_ncc": _apply_stroke_no_block_one_ncc,
        "stroke_total_service": _apply_stroke_total_service,
    }


def _add_by_strength(o, constraint, expression):
    if constraint.strength is ConstraintStrength.HARD:
        o.add(expression)
    elif constraint.strength is ConstraintStrength.SOFT:
        o.add_soft(expression)
    else:
        raise ValueError(f"Constraint strength {constraint.strength.value} is not valid for {constraint.kind}")


def _apply_full_assignment(o, x, context, constraint, fellow_indices):
    range_fellows_assigned_fully(o, x, context.shifts, fellow_indices=fellow_indices)


def _apply_ncc_coverage(o, x, context, constraint, fellow_indices):
    total_fellows = context.fellow_mapping.total_fellows
    max_ncc_fellows = constraint.params.get("max_ncc_fellows", 3)
    max_ncc_plus_swing_fellows = constraint.params.get("max_ncc_plus_swing_fellows", 4)
    for w in range(context.week_count):
        o.add(Sum([If(x[f, w, "NCC1"], 1, 0) for f in range(total_fellows)]) >= 1)
        o.add(Sum([If(x[f, w, "NCC2"], 1, 0) for f in range(total_fellows)]) >= 1)
        o.add(Sum([If(x[f, w, "NCC1"], 1, 0) for f in range(total_fellows)]) <= 2)
        o.add(Sum([If(x[f, w, "NCC2"], 1, 0) for f in range(total_fellows)]) <= 2)
        o.add(Sum([If(Or(x[f, w, "NCC1"], x[f, w, "NCC2"]), 1, 0) for f in range(total_fellows)]) <= max_ncc_fellows)
        o.add(Sum([If(Or(x[f, w, "NCC1"], x[f, w, "NCC2"], x[f, w, "Swing"]), 1, 0) for f in range(total_fellows)]) <= max_ncc_plus_swing_fellows)
        o.add(Sum([If(x[f, w, "Swing"], 1, 0) for f in range(total_fellows)]) <= 1)

    if "swing_deficit" in constraint.params:
        o.add(Sum([
            Sum([If(x[f, w, "Swing"], 1, 0) for f in range(total_fellows)])
            for w in range(context.week_count)
        ]) >= context.week_count - constraint.params["swing_deficit"])


def _apply_minimize_uncovered_shift_weeks(o, x, context, constraint, fellow_indices):
    if not fellow_indices:
        return
    uncovered_week_count = Sum([
        If(
            Or(*[
                x[f, week, shift]
                for f in fellow_indices
                for shift in constraint.shifts.shifts
            ]),
            0,
            1,
        )
        for week in range(context.week_count)
    ])
    o.minimize(uncovered_week_count)


def _apply_max_consecutive(o, x, context, constraint, fellow_indices):
    if constraint.strength is ConstraintStrength.SOFT:
        maximum_consecutive_icu_shifts_soft(
            o,
            x,
            fellow_indices=fellow_indices,
            shifts=list(constraint.shifts.shifts),
            MAX_CONSEC=constraint.params["weeks"],
        )
    else:
        maximum_consecutive_icu_shifts(
            o,
            x,
            fellow_indices=fellow_indices,
            shifts=list(constraint.shifts.shifts),
            MAX_CONSEC=constraint.params["weeks"],
        )


def _apply_jr_first_month_micu(o, x, context, constraint, fellow_indices):
    jr_first_month_micu(o, x, fellow_indices=fellow_indices)


def _apply_jr_ncc_before_week(o, x, context, constraint, fellow_indices):
    threshold = constraint.params["week"] if "week" in constraint.params else constraint.weeks.start
    for f in fellow_indices:
        _add_by_strength(
            o,
            constraint,
            Sum([If(Or(x[f, w, "NCC1"], x[f, w, "NCC2"]), 1, 0) for w in range(4, threshold)]) >= 1,
        )


def _apply_jr_ncc_before_swing(o, x, context, constraint, fellow_indices):
    jr_fellows_n_ncc_before_swing(
        o,
        x,
        fellow_indices=fellow_indices,
        n=constraint.params.get("ncc_weeks", 4),
    )


def _apply_all_or_none_block(o, x, context, constraint, fellow_indices):
    block_size = constraint.params["block_size"]
    for shift in constraint.shifts.shifts:
        if constraint.strength is ConstraintStrength.SOFT:
            shift_blocked_soft(o, x, shift, fellow_indices=fellow_indices, GRANULARITY=block_size)
        else:
            shift_blocked(o, x, shift, fellow_indices=fellow_indices, GRANULARITY=block_size)


def _apply_ncc_stroke_oversight(o, x, context, constraint, fellow_indices):
    ncc_stroke_oversight(o, x, fellow_indices=fellow_indices)


def _apply_specific_assignment(o, x, context, constraint, fellow_indices):
    if not fellow_indices:
        return
    week = constraint.weeks.start
    shift = constraint.shifts.shifts[0]
    if constraint.strength is ConstraintStrength.SOFT:
        specific_assignment_soft(o, x, shift=shift, fellow_indices=fellow_indices, week=week)
    else:
        specific_assignment(o, x, shift=shift, fellow_indices=fellow_indices, week=week)


def _apply_fourth_block_two_micu_fellows(o, x, context, constraint, fellow_indices):
    fourth_block_two_micu_fellows(o, x, fellow_indices=fellow_indices)


def _apply_isc(o, x, context, constraint, fellow_indices):
    target_date = tuple(constraint.params.get("date", (2026, 2, 5)))
    target_week = date_to_week_index(target_date)
    for f in fellow_indices:
        for w in range(context.week_count):
            _add_by_strength(o, constraint, x[f, w, "ISC"] if w == target_week else Not(x[f, w, "ISC"]))


def _apply_stroke_no_block_one_ncc(o, x, context, constraint, fellow_indices):
    stroke_no_block_one_ncc(o, x, fellow_indices=fellow_indices)


def _apply_nh_total_service(o, x, context, constraint, fellow_indices):
    nh_total_service(o, x, fellow_indices=fellow_indices)


def _apply_stroke_shift_coverage(o, x, context, constraint, fellow_indices):
    stroke_shifts_covered(o, x, context.fellow_mapping.total_fellows, context.fellow_mapping)


def _apply_comparable_half_year_distribution(o, x, context, constraint, fellow_indices):
    comparable_amounts_each_half_year(o, x, fellow_indices=fellow_indices)


def _apply_nir_one_week_per_half(o, x, context, constraint, fellow_indices):
    nir_one_week_per_half(o, x, fellow_indices=fellow_indices)


def _apply_scvmc_second_half(o, x, context, constraint, fellow_indices):
    scvmc_second_half(o, x, fellow_indices=fellow_indices)


def _apply_stroke_total_service(o, x, context, constraint, fellow_indices):
    stroke_total_service(o, x, fellow_indices=fellow_indices)


def _apply_ncc_jr_total_service(o, x, context, constraint, fellow_indices):
    ncc_jr_total_service(o, x, fellow_indices=fellow_indices)


def _apply_ncc_sr_total_service(o, x, context, constraint, fellow_indices):
    ncc_sr_total_service(o, x, fellow_indices=fellow_indices)


def _apply_ccm_total_service(o, x, context, constraint, fellow_indices):
    ccm_total_service(o, x, fellow_indices=fellow_indices)


def _default_standing_constraints():
    data = yaml.safe_load(STANDING_RULE_CONFIG.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{STANDING_RULE_CONFIG} must contain a YAML mapping.")
    return standing_constraints_from_config(data)


def optimize_schedule(
    fellow_groups: Dict[str, List[str]],
    shifts: List[str],
    fellow_week_pairs: Dict[str, List[int]],
    constraints=None,
):
    # Create the fellow mapping
    fellow_mapping = FellowMapping()
    for group, fellows in fellow_groups.items():
        for name in fellows:
            fellow_mapping.add_fellow(name, group)

    # Create the base schedule constraints
    schedule = ScheduleConstraints(fellow_mapping, W, shifts)
    o = Optimize()
    o.set(timeout=1800000)

    # Add fundamental constraints
    schedule.add_fundamental_constraints(o)

    rule_context = RuleApplicationContext(fellow_mapping, shifts=shifts, week_count=W)
    active_constraints = _default_standing_constraints() if constraints is None else constraints
    apply_constraints(o, schedule.x, active_constraints, rule_context, _rule_handlers())

    status = o.check()
    if status != sat:
        o.unsat_core() 
        raise ValueError(f"Schedule solver returned {status}")

    m = o.model()

    # Process the model using the schedule variables
    shifts_for_fellows = {
        fellow_name: ["" for w in range(W)] 
        for fellow_name in fellow_mapping._fellows.keys()
    }

    fellows_for_shifts = {
        'NCC1': [[] for w in range(W)],
        'NCC2': [[] for w in range(W)],
        'Extra': ['' for w in range(W)],
        'Swing': [[] for w in range(W)],
        'Stroke': [[] for w in range(W)],
        'Stroke_Supervisory': [[] for w in range(W)],
        'Clinic/Elective': [[] for w in range(W)],
        'Telestroke/Clinic': [[] for w in range(W)],
    }

    for d in m.decls():
        if m[d]:
            _, f, w, s = d.name().split('_')
            fellow_name = fellow_mapping.get_fellow_name(int(f))
            if fellow_name is not None:
                shifts_for_fellows[fellow_name][int(w)] = s
                if s in fellows_for_shifts:
                    fellows_for_shifts[s][int(w)].append(fellow_name)

    # Process extra assignments
    for s, v in fellows_for_shifts.items():
        if s not in {'NCC1', 'NCC2', 'Extra'}: continue
        for ii, its in enumerate(v):
            if type(its) is list and len(its) == 0:
                fellows_for_shifts[s][ii] = ""
            elif type(its) is list and len(its) == 1:
                fellows_for_shifts[s][ii] = its[0]
            elif type(its) is list and len(its) == 2:
                # Sort by fellow type to maintain consistent ordering
                sorted_fellows = sorted(its, 
                                     key=lambda name: fellow_mapping.get_fellow_group_rank(name))
                fellows_for_shifts['Extra'][ii] = sorted_fellows[-1]
                fellows_for_shifts[s][ii] = sorted_fellows[0]
            elif (type(its) is list and len(its) > 2) or (type(its) is list and s == 'Extra'):
                print(f"Warning: overassignment in {s}:")
                print(f"at {ii} we have assigned {its}")
            # elif type(its) is list and len(its) > 2:
            #     print(s, ii)
            #     print(its)
            #     quit()

    # Process stroke assignments
    for s, v in fellows_for_shifts.items():
        if s not in {'Stroke'}: continue
        for ii, its in enumerate(v):
            if type(its) is list and len(its) == 0:
                fellows_for_shifts[s][ii] = ""
            elif type(its) is list and len(its) == 1:
                fellows_for_shifts[s][ii] = its[0]
            else:
                print(f"Warning: overassignment in {s}:")
                print(f"at {ii} we have assigned {its}")
            # elif type(its) is list and len(its) == 2:
            #     if 'NH Adam' in its:
            #         fellows_for_shifts['Stroke_Supervisory'][ii] = 'Stroke Victoria'
            #         fellows_for_shifts[s][ii] = 'NH Adam'
            # elif type(its) is list and len(its) > 2:
            #     print(s, ii)
            #     print(its)
            #     quit()

    return shifts_for_fellows, fellows_for_shifts

if __name__ == "__main__":
    try:
        from .cli import main as cli_main
    except ImportError:  # pragma: no cover - supports running from src/scheduler
        from cli import main as cli_main

    raise SystemExit(cli_main())
