"""Strength — the dial that decides what an evaluated violation MEANS.

A Constraint and a Criterion are the same object differentiated by Strength:
HARD ⇒ a violation makes the Schedule invalid (encoded as a forbidding
constraint); SOFT ⇒ a violation is a penalty the solver minimizes (encoded as a
weighted indicator). See CONTEXT.md and ADR-0005.
"""

from __future__ import annotations

from enum import Enum


class Strength(Enum):
    HARD = "hard"
    SOFT = "soft"


HARD = Strength.HARD
SOFT = Strength.SOFT
