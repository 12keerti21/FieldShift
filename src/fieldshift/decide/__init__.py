"""Decision layer: generate candidate rotations, score them, find the trade-offs.

Everything here operates on simulated outcomes. Nothing in this package computes
agronomy - it searches over sequences and ranks what the physical models produced,
which keeps the ranking auditable and the science separable from the preferences.
"""

from .candidates import (
    CandidateSet,
    RotationConstraints,
    YearPattern,
    build_rotation,
    canonical_cycle,
    enumerate_rotations,
)
from .objectives import OBJECTIVES, Objective, RotationScore, score_rotation, select_objectives
from .pareto import dominated_by, dominates, pareto_front, trade_off, weight_sensitivity, weighted_ranking
from .search import SearchResult, evaluate_plan, search_rotations

__all__ = [
    "OBJECTIVES",
    "CandidateSet",
    "Objective",
    "RotationConstraints",
    "RotationScore",
    "SearchResult",
    "YearPattern",
    "build_rotation",
    "canonical_cycle",
    "dominated_by",
    "dominates",
    "enumerate_rotations",
    "evaluate_plan",
    "pareto_front",
    "score_rotation",
    "search_rotations",
    "select_objectives",
    "trade_off",
    "weight_sensitivity",
    "weighted_ranking",
]
