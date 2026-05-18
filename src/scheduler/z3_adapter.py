from __future__ import annotations

from z3 import AtMost, Bool, If, Optimize, Or, is_true

from .semantic_constraints import (
    ConstraintStrength,
    FellowSelector,
    SemanticConstraint,
    ShiftSet,
    WeekSpan,
)


class Z3ScheduleAdapter:
    def __init__(self, fellows: dict[str, str], num_weeks: int, shifts: list[str]):
        self.fellows = dict(fellows)
        self.num_weeks = num_weeks
        self.shifts = tuple(shifts)
        self.solver = Optimize()
        self.x = {
            (fellow, week, shift): Bool(f"x_{fellow}_{week}_{shift}")
            for fellow in self.fellows
            for week in range(num_weeks)
            for shift in self.shifts
        }
        self._model = None

    def compile(self, constraints: list[SemanticConstraint]) -> None:
        for constraint in constraints:
            self._compile_one(constraint)

    def check(self):
        status = self.solver.check()
        self._model = self.solver.model() if str(status) == "sat" else None
        return status

    def model_value(self, fellow_name: str, week: int, shift: str) -> bool:
        if self._model is None:
            raise ValueError("No satisfiable model is available. Call check() first.")
        return is_true(self._model.evaluate(self.variable(fellow_name, week, shift), model_completion=True))

    def variable(self, fellow_name: str, week: int, shift: str):
        return self.x[fellow_name, week, shift]

    def _compile_one(self, constraint: SemanticConstraint) -> None:
        if constraint.kind == "specific_assignment":
            self._specific_assignment(constraint)
        elif constraint.kind == "at_most_one_shift_per_week":
            self._at_most_one_shift_per_week(constraint)
        elif constraint.kind == "all_or_none_block":
            self._all_or_none_block(constraint)
        elif constraint.kind == "minimize_uncovered_shift_weeks":
            self._minimize_uncovered_shift_weeks(constraint)
        else:
            raise NotImplementedError(f"Unsupported semantic constraint kind: {constraint.kind}")

    def _specific_assignment(self, constraint: SemanticConstraint) -> None:
        fellows = self._selected_fellows(_require(constraint.fellows, "fellows"))
        weeks = _require(constraint.weeks, "weeks")
        shifts = _require(constraint.shifts, "shifts")
        expression = Or(*[
            self.variable(fellow, week, shift)
            for fellow in fellows
            for week in weeks.weeks()
            for shift in shifts.shifts
        ])
        self._add(expression, constraint.strength)

    def _at_most_one_shift_per_week(self, constraint: SemanticConstraint) -> None:
        fellows = self._selected_fellows(_require(constraint.fellows, "fellows"))
        weeks = _require(constraint.weeks, "weeks")
        shifts = _require(constraint.shifts, "shifts")
        for fellow in fellows:
            for week in weeks.weeks():
                self.solver.add(AtMost(*[self.variable(fellow, week, shift) for shift in shifts.shifts], 1))

    def _all_or_none_block(self, constraint: SemanticConstraint) -> None:
        fellows = self._selected_fellows(_require(constraint.fellows, "fellows"))
        weeks = _require(constraint.weeks, "weeks")
        shifts = _require(constraint.shifts, "shifts")
        block_size = constraint.params["block_size"]
        for fellow in fellows:
            for block_start in range(weeks.start, weeks.end, block_size):
                block_weeks = range(block_start, min(block_start + block_size, weeks.end))
                for shift in shifts.shifts:
                    count = sum(If(self.variable(fellow, week, shift), 1, 0) for week in block_weeks)
                    self._add(Or(count == 0, count == len(list(block_weeks))), constraint.strength)

    def _minimize_uncovered_shift_weeks(self, constraint: SemanticConstraint) -> None:
        fellows = self._selected_fellows(_require(constraint.fellows, "fellows"))
        weeks = _require(constraint.weeks, "weeks") if constraint.weeks else WeekSpan(0, self.num_weeks)
        shifts = _require(constraint.shifts, "shifts")
        uncovered_week_count = sum(
            If(
                Or(*[self.variable(fellow, week, shift) for fellow in fellows for shift in shifts.shifts]),
                0,
                1,
            )
            for week in weeks.weeks()
        )
        self.solver.minimize(uncovered_week_count)

    def _add(self, expression, strength: ConstraintStrength) -> None:
        if strength is ConstraintStrength.HARD:
            self.solver.add(expression)
        elif strength is ConstraintStrength.SOFT:
            self.solver.add_soft(expression)
        else:
            raise ValueError(f"Constraint strength {strength.value} is not valid for boolean assertions.")

    def _selected_fellows(self, selector: FellowSelector) -> tuple[str, ...]:
        if selector.names:
            return selector.names
        return tuple(
            fellow_name
            for fellow_name, fellow_type in self.fellows.items()
            if fellow_type in selector.groups
        )


def _require(value, label: str):
    if value is None:
        raise ValueError(f"Semantic constraint requires {label}.")
    return value
