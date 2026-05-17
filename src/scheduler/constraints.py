from typing import List, Dict
from z3 import *
try:
    from .fellow_mapping import FellowMapping, FellowType
except ImportError:  # pragma: no cover - supports running from src/scheduler
    from fellow_mapping import FellowMapping, FellowType

class ScheduleConstraints:
    def __init__(self, fellow_mapping: FellowMapping, num_weeks: int, shifts: List[str]):
        self.fellow_mapping = fellow_mapping
        self.num_weeks = num_weeks
        self.shifts = shifts
        self.x = self._create_variables()
        
    def _create_variables(self):
        """Create the basic 3D boolean variables for the schedule."""
        return {
            (f, w, r): Bool(f"x_{f}_{w}_{r}") 
            for f in self.fellow_mapping.all_fellow_indices
            for w in range(self.num_weeks) 
            for r in self.shifts
        }
    
    def add_fundamental_constraints(self, o: Optimize):
        """Add all fundamental constraints that must hold for any valid schedule."""
        self._add_one_rotation_per_week(o)
        self._add_ncc_coverage(o)
        self._add_symmetry_breaking(o)
        
    def _add_one_rotation_per_week(self, o: Optimize):
        """Each person can only do one thing at a time."""
        for f in self.fellow_mapping.all_fellow_indices:
            for w in range(self.num_weeks):
                o.add(AtMost(*[self.x[f, w, r] for r in self.shifts], 1))
                
    def _add_ncc_coverage(self, o: Optimize):
        """Basic coverage requirements for NCC shifts."""
        for w in range(self.num_weeks):
            # At least one fellow on NCC1 and NCC2 each week
            o.add(Sum([If(self.x[f, w, "NCC1"], 1, 0) for f in self.fellow_mapping.all_fellow_indices]) >= 1)
            o.add(Sum([If(self.x[f, w, "NCC2"], 1, 0) for f in self.fellow_mapping.all_fellow_indices]) >= 1)
            # At most two fellows on each NCC shift
            o.add(Sum([If(self.x[f, w, "NCC1"], 1, 0) for f in self.fellow_mapping.all_fellow_indices]) <= 2)
            o.add(Sum([If(self.x[f, w, "NCC2"], 1, 0) for f in self.fellow_mapping.all_fellow_indices]) <= 2)
            # At most one fellow on swing
            o.add(Sum([If(self.x[f, w, "Swing"], 1, 0) for f in self.fellow_mapping.all_fellow_indices]) <= 1)
            
    def _add_symmetry_breaking(self, o: Optimize):
        """Add symmetry-breaking constraints to help the solver."""
        # For CCM fellows, break symmetry by assigning them to blocks in alphabetical order
        # This is a fundamental constraint because it's about the representation, not the schedule
        ccm_fellows = self.fellow_mapping.get_fellows_by_type(FellowType.CCM)
        for fellow, w in zip(ccm_fellows, range(0, self.num_weeks, 4)):
            # Each CCM fellow works only in their assigned block
            for w_ in range(self.num_weeks):
                if w_ < w or w_ > w + 3:
                    for s in ["NCC1", "NCC2", "Swing"]:
                        o.add(Not(self.x[fellow.index, w_, s]))
                        
    def get_variable(self, fellow_name: str, week: int, shift: str) -> BoolRef:
        """Get the Z3 variable for a specific assignment."""
        fellow_index = self.fellow_mapping.get_fellow_index(fellow_name)
        return self.x[fellow_index, week, shift]
        
    def get_all_variables(self) -> Dict[tuple, BoolRef]:
        """Get all Z3 variables."""
        return self.x 
