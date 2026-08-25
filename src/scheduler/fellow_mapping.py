from dataclasses import dataclass
from typing import Dict, List, Optional

@dataclass
class Fellow:
    name: str
    group: str
    index: int  # Index in the optimization variables

class FellowMapping:
    def __init__(self):
        self._fellows: Dict[str, Fellow] = {}
        self._group_to_fellows: Dict[str, List[Fellow]] = {}
        self._group_order: Dict[str, int] = {}
        self._next_index = 0
        
    def add_fellow(self, name: str, group: str) -> Fellow:
        """Add a fellow to the mapping and return their Fellow object."""
        if name in self._fellows:
            raise ValueError(f"Fellow {name} already exists in mapping")
            
        if group not in self._group_order:
            self._group_order[group] = len(self._group_order)

        fellow = Fellow(name=name, group=group, index=self._next_index)
        self._fellows[name] = fellow
        self._group_to_fellows.setdefault(group, []).append(fellow)
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
    
    def get_fellows_by_group(self, group: str) -> List[Fellow]:
        """Get all fellows in a named group."""
        return self._group_to_fellows.get(group, [])
    
    def get_fellow_indices_by_group(self, group: str) -> List[int]:
        """Get all fellow indices in a named group."""
        return [f.index for f in self.get_fellows_by_group(group)]
    
    def get_fellow_indices_by_groups(self, *groups: str) -> List[int]:
        """Get all fellow indices in named groups, preserving group order."""
        indices = []
        for group in groups:
            indices.extend(self.get_fellow_indices_by_group(group))
        return indices

    def get_fellow_group_rank(self, name: str) -> int:
        fellow = self.get_fellow(name)
        if fellow is None:
            raise ValueError(f"Fellow {name} not found in mapping")
        return self._group_order[fellow.group]
    
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
