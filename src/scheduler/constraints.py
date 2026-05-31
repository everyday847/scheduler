from typing import List, Dict
try:
    from z3 import *
except ImportError:
    pass
try:
    from .fellow_mapping import FellowMapping
except ImportError:  # pragma: no cover - supports running from src/scheduler
    from fellow_mapping import FellowMapping

class ScheduleConstraints:
    def __init__(
        self,
        fellow_mapping: FellowMapping,
        num_weeks: int,
        shifts: List[str],
        forbidden_assignments: set[tuple[int, int, str]] | None = None,
    ):
        self.fellow_mapping = fellow_mapping
        self.num_weeks = num_weeks
        self.shifts = shifts
        self.forbidden_assignments = forbidden_assignments or set()
        self.x = self._create_variables()
        
    def _create_variables(self):
        """Create the basic 3D boolean variables for the schedule."""
        return {
            (f, w, r): (
                BoolVal(False)
                if (f, w, r) in self.forbidden_assignments
                else Bool(f"x_{f}_{w}_{r}")
            )
            for f in self.fellow_mapping.all_fellow_indices
            for w in range(self.num_weeks) 
            for r in self.shifts
        }
    
    def add_fundamental_constraints(self, o: Optimize):
        """Add all fundamental constraints that must hold for any valid schedule."""
        self._add_one_rotation_per_week(o)
        
    def _add_one_rotation_per_week(self, o: Optimize):
        """Each person can only do one thing at a time."""
        for f in self.fellow_mapping.all_fellow_indices:
            for w in range(self.num_weeks):
                o.add(AtMost(*[self.x[f, w, r] for r in self.shifts], 1))
                        
    def get_variable(self, fellow_name: str, week: int, shift: str) -> BoolRef:
        """Get the Z3 variable for a specific assignment."""
        fellow_index = self.fellow_mapping.get_fellow_index(fellow_name)
        return self.x[fellow_index, week, shift]
        
    def get_all_variables(self) -> Dict[tuple, BoolRef]:
        """Get all Z3 variables."""
        return self.x 
