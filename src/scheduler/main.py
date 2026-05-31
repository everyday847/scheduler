from typing import List, Dict

try:
    from z3 import *
except ImportError:
    pass
import openpyxl
from pathlib import Path
import yaml

import tqdm

from .date_to_week_index import date_to_week_index
from .constraints import ScheduleConstraints
from .fellow_mapping import FellowMapping
from .rule_application import RuleApplicationContext, apply_constraints
from .semantic_constraints import ConstraintStrength
from .standing_rules import constraints_from_config as standing_constraints_from_config

try:
    set_option(verbose=10)
except NameError:
    pass

# Not too dangerous to make global
W = 52
STANDING_RULE_CONFIG = Path(__file__).resolve().parents[2] / "config" / "standing" / "stanford-fellowship.yaml"

def stroke_shifts_covered(o, x, total_fellows, fellow_mapping):
    # Every week has exactly one person on Stroke and on Telestroke/Clinic
    for w in range(W):
        o.add(_count_relation([x[f, w, "Stroke"] for f in range(total_fellows)], "exactly", 1))
        o.add(_count_relation([x[f, w, "Telestroke/Clinic"] for f in range(total_fellows)], "exactly", 1))

def maximum_consecutive_icu_shifts(o, x, fellow_indices, shifts, MAX_CONSEC):
    for f in fellow_indices:
        for w in range(W - MAX_CONSEC):
            o.add(_count_relation([
                Or(*[x[f, w + i, r] for r in shifts])
                for i in range(MAX_CONSEC + 1)
            ], "at_most", MAX_CONSEC))

def maximum_consecutive_icu_shifts_soft(o, x, fellow_indices, shifts, MAX_CONSEC):
    for f in fellow_indices:
        for w in range(W - MAX_CONSEC):
            o.add_soft(_count_relation([
                Or(*[x[f, w + i, r] for r in shifts])
                for i in range(MAX_CONSEC + 1)
            ], "at_most", MAX_CONSEC))

def jr_fellows_n_ncc_before_swing(o, x, fellow_indices, n, week_count=W):
    for f in fellow_indices:
        for w in range(week_count):
            previous_ncc = [
                Or(x[f, prior_week, "NCC1"], x[f, prior_week, "NCC2"])
                for prior_week in range(w)
            ]
            o.add(Implies(x[f, w, "Swing"], _count_relation(previous_ncc, "at_least", n)))

def shift_blocked(o, x, shift, fellow_indices, GRANULARITY):
    # junior fellows have 4 sicu, and it should follow a block.
    for f in fellow_indices:
        # Consecutivity
        for w in range(0, W, GRANULARITY):
            # Either all or none of the block is SICU.
            shift_terms = _shift_terms(x, f, range(w, w + GRANULARITY), [shift])
            o.add(
                Or(
                    _count_relation(shift_terms, "exactly", GRANULARITY),
                    _count_relation(shift_terms, "exactly", 0),
                )
            )

def shift_blocked_soft(o, x, shift, fellow_indices, GRANULARITY):
    for f in fellow_indices:
        # Consecutivity
        for w in range(0, W, GRANULARITY):
            # Either all or none of the block is SICU.
            shift_terms = _shift_terms(x, f, range(w, w + GRANULARITY), [shift])
            o.add_soft(
                Or(
                    _count_relation(shift_terms, "exactly", GRANULARITY),
                    _count_relation(shift_terms, "exactly", 0),
                )
            )

def fourth_block_two_micu_fellows(o, x, fellow_indices):
    # from the fellows between start and end, ensure MICU is double-staffed for every week from 12-15
    for w in range(12,16):
        o.add(
            _count_relation([x[f, w, "MICU"] for f in fellow_indices], "exactly", 2),
        )

def nir_one_week_per_half(o, x, fellow_indices):
    for f in fellow_indices:
        o.add(
            _count_relation([x[f, w, "NIR"] for w in range(0, W // 2)], "exactly", 1)
        )
        o.add(
            _count_relation([x[f, w, "NIR"] for w in range(W // 2, W)], "exactly", 1)
        )

def scvmc_second_half(o, x, fellow_indices):
    for f in fellow_indices:
        o.add(
            _count_relation([x[f, w, "SCVMC Rehab"] for w in range(W // 2, W)], "exactly", 2)
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

def stroke_no_block_one_ncc(o, x, fellow_indices):
    for f in fellow_indices:
        for w in range(4):
            if hasattr(o, "add_soft"):
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

def _rule_handlers():
    return {
        "all_or_none_block": _apply_all_or_none_block,
        "block_shift_count": _apply_block_shift_count,
        "block_shift_set_choice": _apply_block_shift_set_choice,
        "comparable_half_year_distribution": _apply_comparable_half_year_distribution,
        "full_assignment": _apply_full_assignment,
        "fourth_block_two_micu_fellows": _apply_fourth_block_two_micu_fellows,
        "isc": _apply_isc,
        "jr_ncc_before_swing": _apply_jr_ncc_before_swing,
        "max_consecutive": _apply_max_consecutive,
        "minimize_uncovered_shift_weeks": _apply_minimize_uncovered_shift_weeks,
        "ncc_coverage": _apply_ncc_coverage,
        "ncc_stroke_oversight": _apply_ncc_stroke_oversight,
        "nir_one_week_per_half": _apply_nir_one_week_per_half,
        "scvmc_second_half": _apply_scvmc_second_half,
        "service_profile": _apply_service_profile,
        "specific_assignment": _apply_specific_assignment,
        "stroke_shift_coverage": _apply_stroke_shift_coverage,
        "stroke_no_block_one_ncc": _apply_stroke_no_block_one_ncc,
    }


def _add_by_strength(o, constraint, expression):
    if is_true(simplify(expression)):
        return
    if constraint.strength is ConstraintStrength.HARD:
        o.add(expression)
    elif constraint.strength is ConstraintStrength.SOFT:
        o.add_soft(expression)
    else:
        raise ValueError(f"Constraint strength {constraint.strength.value} is not valid for {constraint.kind}")


def _require_hard_constraint(constraint):
    if constraint.strength is not ConstraintStrength.HARD:
        raise ValueError(f"{constraint.kind} currently supports only hard strength.")


def _apply_full_assignment(o, x, context, constraint, fellow_indices):
    for f in fellow_indices:
        for w in range(context.week_count):
            _add_by_strength(o, constraint, AtLeast(*[x[f, w, r] for r in context.shifts], 1))


def _apply_ncc_coverage(o, x, context, constraint, fellow_indices):
    total_fellows = context.fellow_mapping.total_fellows
    max_ncc_fellows = constraint.params.get("max_ncc_fellows", 3)
    max_ncc_plus_swing_fellows = constraint.params.get("max_ncc_plus_swing_fellows", 4)
    for w in range(context.week_count):
        ncc1_assignments = [x[f, w, "NCC1"] for f in range(total_fellows)]
        ncc2_assignments = [x[f, w, "NCC2"] for f in range(total_fellows)]
        ncc_assignments = [Or(x[f, w, "NCC1"], x[f, w, "NCC2"]) for f in range(total_fellows)]
        ncc_plus_swing_assignments = [
            Or(x[f, w, "NCC1"], x[f, w, "NCC2"], x[f, w, "Swing"])
            for f in range(total_fellows)
        ]
        swing_assignments = [x[f, w, "Swing"] for f in range(total_fellows)]

        _add_by_strength(o, constraint, _count_relation(ncc1_assignments, "at_least", 1))
        _add_by_strength(o, constraint, _count_relation(ncc2_assignments, "at_least", 1))
        _add_by_strength(o, constraint, _count_relation(ncc1_assignments, "at_most", 2))
        _add_by_strength(o, constraint, _count_relation(ncc2_assignments, "at_most", 2))
        _add_by_strength(o, constraint, _count_relation(ncc_assignments, "at_most", max_ncc_fellows))
        _add_by_strength(o, constraint, _count_relation(ncc_plus_swing_assignments, "at_most", max_ncc_plus_swing_fellows))
        _add_by_strength(o, constraint, _count_relation(swing_assignments, "at_most", 1))

    if "swing_deficit" in constraint.params:
        _add_by_strength(o, constraint, Sum([
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


def _apply_jr_ncc_before_swing(o, x, context, constraint, fellow_indices):
    jr_fellows_n_ncc_before_swing(
        o,
        x,
        fellow_indices=fellow_indices,
        n=constraint.params.get("ncc_weeks", 4),
        week_count=context.week_count,
    )


def _apply_all_or_none_block(o, x, context, constraint, fellow_indices):
    block_size = constraint.params["block_size"]
    for shift in constraint.shifts.shifts:
        if constraint.strength is ConstraintStrength.SOFT:
            shift_blocked_soft(o, x, shift, fellow_indices=fellow_indices, GRANULARITY=block_size)
        else:
            shift_blocked(o, x, shift, fellow_indices=fellow_indices, GRANULARITY=block_size)


def _apply_block_shift_count(o, x, context, constraint, fellow_indices):
    shifts = list(constraint.shifts.shifts) if constraint.shifts else constraint.params["shifts"]
    block_size = constraint.params["block_size"]
    allowed_counts = constraint.params["allowed_counts"]
    for f in fellow_indices:
        for block_start in range(0, context.week_count, block_size):
            block_weeks = range(block_start, min(block_start + block_size, context.week_count))
            shift_terms = _shift_terms(x, f, block_weeks, shifts)
            _add_by_strength(o, constraint, Or(*[
                _count_relation(shift_terms, "exactly", allowed)
                for allowed in allowed_counts
            ]))


def _apply_block_shift_set_choice(o, x, context, constraint, fellow_indices):
    block_size = constraint.params["block_size"]
    choices = constraint.params["choices"]
    trigger_shifts = sorted({shift for choice in choices for shift in choice})
    for f in fellow_indices:
        for block_start in range(0, context.week_count, block_size):
            block_weeks = range(block_start, min(block_start + block_size, context.week_count))
            block_length = len(list(block_weeks))
            choice_expressions = [
                _count_relation(_shift_terms(x, f, block_weeks, choice), "exactly", block_length)
                for choice in choices
            ]
            if constraint.params.get("allow_none", False):
                choice_expressions.append(
                    _count_relation(_shift_terms(x, f, block_weeks, trigger_shifts), "exactly", 0)
                )
            _add_by_strength(o, constraint, Or(*choice_expressions))


def _apply_service_profile(o, x, context, constraint, fellow_indices):
    for f in fellow_indices:
        for shift in constraint.params.get("zero_shifts", []):
            for week in range(context.week_count):
                _add_by_strength(o, constraint, Not(x[f, week, shift]))

        for total in constraint.params.get("totals", []):
            _add_by_strength(
                o,
                constraint,
                _count_relation(
                    _shift_terms(x, f, range(context.week_count), total["shifts"]),
                    total["relation"],
                    total["weeks"],
                ),
            )

        for total in constraint.params.get("window_totals", []):
            window_start, window_end = total["window"]
            _add_by_strength(
                o,
                constraint,
                _count_relation(
                    _shift_terms(x, f, range(window_start, window_end), total["shifts"]),
                    total["relation"],
                    total["weeks"],
                ),
            )

        for active_block in constraint.params.get("active_blocks", []):
            block_size = active_block["block_size"]
            for block_start in range(0, context.week_count, block_size):
                block_weeks = range(block_start, min(block_start + block_size, context.week_count))
                trigger_terms = _shift_terms(x, f, block_weeks, active_block["trigger_shifts"])
                block_conditions = [
                    _count_relation(
                        _shift_terms(x, f, block_weeks, count_rule["shifts"]),
                        count_rule["relation"],
                        count_rule["weeks"],
                    )
                    for count_rule in active_block["counts"]
                ]
                _add_by_strength(o, constraint, Implies(Or(*trigger_terms), And(*block_conditions)))


def _apply_ncc_stroke_oversight(o, x, context, constraint, fellow_indices):
    for w in range(context.week_count):
        _add_by_strength(
            o,
            constraint,
            _count_relation([
                Or(x[f, w, "NCC1"], x[f, w, "NCC2"])
                for f in fellow_indices
            ], "at_least", 1),
        )


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
    # _require_hard_constraint(constraint)
    fourth_block_two_micu_fellows(o, x, fellow_indices=fellow_indices)


def _apply_isc(o, x, context, constraint, fellow_indices):
    target_date = tuple(constraint.params.get("date", (2026, 2, 5)))
    target_week = date_to_week_index(target_date)
    for f in fellow_indices:
        for w in range(context.week_count):
            _add_by_strength(o, constraint, x[f, w, "ISC"] if w == target_week else Not(x[f, w, "ISC"]))


def _apply_stroke_no_block_one_ncc(o, x, context, constraint, fellow_indices):
    # _require_hard_constraint(constraint)
    stroke_no_block_one_ncc(o, x, fellow_indices=fellow_indices)


def _apply_stroke_shift_coverage(o, x, context, constraint, fellow_indices):
    # _require_hard_constraint(constraint)
    stroke_shifts_covered(o, x, context.fellow_mapping.total_fellows, context.fellow_mapping)


def _apply_comparable_half_year_distribution(o, x, context, constraint, fellow_indices):
    # _require_hard_constraint(constraint)
    comparable_amounts_each_half_year(o, x, fellow_indices=fellow_indices)


def _apply_nir_one_week_per_half(o, x, context, constraint, fellow_indices):
    # _require_hard_constraint(constraint)
    nir_one_week_per_half(o, x, fellow_indices=fellow_indices)


def _apply_scvmc_second_half(o, x, context, constraint, fellow_indices):
    # _require_hard_constraint(constraint)
    scvmc_second_half(o, x, fellow_indices=fellow_indices)


def _shift_count(x, fellow_index, weeks, shifts):
    return Sum([
        If(x[fellow_index, week, shift], 1, 0)
        for week in weeks
        for shift in shifts
    ])


def _shift_terms(x, fellow_index, weeks, shifts):
    return [
        x[fellow_index, week, shift]
        for week in weeks
        for shift in shifts
    ]


def _count_relation(count_or_terms, relation, expected):
    if isinstance(count_or_terms, (list, tuple)):
        return _pseudo_boolean_relation(count_or_terms, relation, expected)

    count = count_or_terms
    if relation == "exactly":
        return count == expected
    if relation == "at_least":
        return count >= expected
    if relation == "at_most":
        return count <= expected
    raise ValueError(f"Unknown service count relation: {relation}")


def _pseudo_boolean_relation(terms, relation, expected):
    weighted_terms = [(term, 1) for term in terms]
    if not weighted_terms:
        return _empty_count_relation(relation, expected)
    if relation == "exactly":
        return PbEq(weighted_terms, expected)
    if relation == "at_least":
        return PbGe(weighted_terms, expected)
    if relation == "at_most":
        return PbLe(weighted_terms, expected)
    raise ValueError(f"Unknown service count relation: {relation}")


def _empty_count_relation(relation, expected):
    if relation == "exactly":
        return BoolVal(expected == 0)
    if relation == "at_least":
        return BoolVal(0 >= expected)
    if relation == "at_most":
        return BoolVal(0 <= expected)
    raise ValueError(f"Unknown service count relation: {relation}")


def _forbidden_assignments_from_constraints(fellow_mapping, shifts, week_count, constraints):
    known_shifts = set(shifts)
    forbidden_assignments = set()

    for constraint in constraints:
        if constraint.strength is not ConstraintStrength.HARD:
            continue
        fellow_indices = _fellow_indices_for_constraint(fellow_mapping, constraint)
        if constraint.kind == "service_profile":
            for shift in constraint.params.get("zero_shifts", []):
                if shift not in known_shifts:
                    continue
                for fellow_index in fellow_indices:
                    for week in range(week_count):
                        forbidden_assignments.add((fellow_index, week, shift))
        elif constraint.kind == "isc" and "ISC" in known_shifts:
            target_date = tuple(constraint.params.get("date", (2026, 2, 5)))
            target_week = date_to_week_index(target_date)
            for fellow_index in fellow_indices:
                for week in range(week_count):
                    if week != target_week:
                        forbidden_assignments.add((fellow_index, week, "ISC"))

    return forbidden_assignments


def _ccm_assigned_weeks(fellow_mapping, fellow_index, week_count):
    ccm_indices = [fellow.index for fellow in fellow_mapping.get_fellows_by_group("CCM")]
    try:
        block_start = ccm_indices.index(fellow_index) * 4
    except ValueError:
        return set()
    return set(range(block_start, min(block_start + 4, week_count)))


def _fellow_indices_for_constraint(fellow_mapping, constraint):
    selector = constraint.fellows
    if selector is None:
        return list(fellow_mapping.all_fellow_indices)
    if selector.groups:
        return fellow_mapping.get_fellow_indices_by_groups(*selector.groups)
    return [
        fellow_mapping.get_fellow_index(name)
        for name in selector.names
        if fellow_mapping.get_fellow(name) is not None
    ]


def _hard_constraints(constraints):
    return [
        constraint
        for constraint in constraints
        if constraint.strength is ConstraintStrength.HARD
    ]


def _check_hard_constraints(schedule, active_constraints, rule_context):
    solver = Solver()
    solver.set(timeout=1800000)
    schedule.add_fundamental_constraints(solver)
    apply_constraints(solver, schedule.x, _hard_constraints(active_constraints), rule_context, _rule_handlers())
    status = solver.check()
    if status != sat:
        raise ValueError(f"Hard schedule constraints returned {status}")


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

    active_constraints = list(_default_standing_constraints() if constraints is None else constraints)
    forbidden_assignments = _forbidden_assignments_from_constraints(
        fellow_mapping,
        shifts,
        W,
        active_constraints,
    )

    # Create the base schedule constraints
    schedule = ScheduleConstraints(fellow_mapping, W, shifts, forbidden_assignments=forbidden_assignments)
    rule_context = RuleApplicationContext(fellow_mapping, shifts=shifts, week_count=W)
    _check_hard_constraints(schedule, active_constraints, rule_context)

    o = Optimize()
    o.set(timeout=1800000)

    # Add fundamental constraints
    schedule.add_fundamental_constraints(o)

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
