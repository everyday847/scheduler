from typing import Dict, List, Set, Optional
from dataclasses import dataclass
from enum import Enum, auto

class FellowType(Enum):
    NCC_JR = auto()
    NCC_SR = auto()
    STROKE = auto()
    CCM = auto()
    NH = auto()
    LIA = auto()

@dataclass
class Fellow:
    name: str
    type: FellowType
    index: int  # Index in the optimization variables

class FellowMapping:
    def __init__(self):
        self._fellows: Dict[str, Fellow] = {}
        self._type_to_fellows: Dict[FellowType, List[Fellow]] = {
            fellow_type: [] for fellow_type in FellowType
        }
        self._next_index = 0
        
    def add_fellow(self, name: str, fellow_type: FellowType) -> Fellow:
        """Add a fellow to the mapping and return their Fellow object."""
        if name in self._fellows:
            raise ValueError(f"Fellow {name} already exists in mapping")
            
        fellow = Fellow(name=name, type=fellow_type, index=self._next_index)
        self._fellows[name] = fellow
        self._type_to_fellows[fellow_type].append(fellow)
        self._next_index += 1
        return fellow
    
    def get_fellow(self, name: str) -> Optional[Fellow]:
        """Get a Fellow object by name."""
        return self._fellows.get(name)
    
    def get_fellow_index(self, name: str) -> int:
        """Get a fellow's index in the optimization variables."""
        fellow = self.get_fellow(name)
        if fellow is None:
            raise ValueError(f"Fellow {name} not found in mapping")
        return fellow.index
    
    def get_fellows_by_type(self, fellow_type: FellowType) -> List[Fellow]:
        """Get all fellows of a particular type."""
        return self._type_to_fellows[fellow_type]
    
    def get_fellow_indices_by_type(self, fellow_type: FellowType) -> List[int]:
        """Get all fellow indices of a particular type."""
        return [f.index for f in self._type_to_fellows[fellow_type]]
    
    def get_fellow_range(self, start_type: FellowType, end_type: FellowType) -> List[int]:
        """Get all fellow indices between two types (inclusive)."""
        indices = []
        for fellow_type in FellowType:
            if fellow_type.value >= start_type.value and fellow_type.value <= end_type.value:
                indices.extend(self.get_fellow_indices_by_type(fellow_type))
        return sorted(indices)
    
    @property
    def total_fellows(self) -> int:
        """Get the total number of fellows."""
        return len(self._fellows)
    
    @property
    def all_fellow_indices(self) -> List[int]:
        """Get all fellow indices in order."""
        return list(range(self.total_fellows))
    
    def get_fellow_name(self, index: int) -> Optional[str]:
        """Get a fellow's name by their index."""
        for fellow in self._fellows.values():
            if fellow.index == index:
                return fellow.name
        return None 