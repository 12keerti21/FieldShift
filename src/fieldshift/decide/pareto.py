"""Non-dominated sets and the trade-offs between them.

The reason this exists rather than a weighted score: a weighted sum collapses a
genuine trade-off into one number and hides the exchange rate. A farmer choosing
between rotations needs to see that saving 40 mm of water costs 0.3 t/ha of
production - not that rotation B scored 7.4.

So the output is the Pareto front plus the explicit cost of moving along it.
Weighted ranking is still available, because at some point a choice has to be made,
but it operates on simulated outcomes rather than on crop attributes and it reports
how sensitive the answer is to the weights.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from .objectives import Objective, RotationScore, objective_vector


class ParetoError(ValueError):
    pass


@dataclass(frozen=True)
class TradeOff:
    """What moving from one rotation to another costs and gains."""

    from_name: str
    to_name: str
    gains: tuple[tuple[Objective, float], ...]
    losses: tuple[tuple[Objective, float], ...]

    def describe(self) -> str:
        def fmt(items):
            parts = []
            for o, delta in items:
                direction = "up" if delta > 0 else "down"
                parts.append(f"{o.label} {direction} {abs(delta):.3g} {o.units}")
            return "; ".join(parts) or "nothing"

        return (
            f"{self.from_name} -> {self.to_name}\n"
            f"      BETTER: {fmt(self.gains)}\n"
            f"      WORSE:  {fmt(self.losses)}"
        )


def dominates(
    a: RotationScore, b: RotationScore, objectives: Sequence[Objective]
) -> bool:
    """True when a is at least as good as b everywhere and strictly better somewhere.

    Ties on every objective count as non-dominating in both directions, so
    genuinely equivalent rotations both survive rather than one being dropped by
    accident of ordering.
    """
    if not objectives:
        raise ParetoError("domination needs at least one objective")
    va, vb = objective_vector(a, objectives), objective_vector(b, objectives)
    at_least_as_good = all(
        o.better(x, y) or x == y for o, x, y in zip(objectives, va, vb)
    )
    strictly_better = any(o.better(x, y) for o, x, y in zip(objectives, va, vb))
    return at_least_as_good and strictly_better


def pareto_front(
    scores: Sequence[RotationScore], objectives: Sequence[Objective]
) -> tuple[int, ...]:
    """Indices of the non-dominated rotations."""
    if not scores:
        return ()
    keep = []
    for i, candidate in enumerate(scores):
        if not any(
            dominates(other, candidate, objectives)
            for j, other in enumerate(scores) if i != j
        ):
            keep.append(i)
    return tuple(keep)


def dominated_by(
    scores: Sequence[RotationScore], index: int, objectives: Sequence[Objective]
) -> tuple[int, ...]:
    """Which rotations beat this one outright. Useful for explaining a rejection."""
    return tuple(
        j for j, other in enumerate(scores)
        if j != index and dominates(other, scores[index], objectives)
    )


def trade_off(
    a: RotationScore, b: RotationScore, objectives: Sequence[Objective]
) -> TradeOff:
    """The exchange rate between two rotations, split into gains and losses."""
    va, vb = objective_vector(a, objectives), objective_vector(b, objectives)
    gains, losses = [], []
    for obj, x, y in zip(objectives, va, vb):
        if y == x:
            continue
        (gains if obj.better(y, x) else losses).append((obj, y - x))
    return TradeOff(a.name, b.name, tuple(gains), tuple(losses))


def normalise(
    scores: Sequence[RotationScore], objectives: Sequence[Objective]
) -> np.ndarray:
    """Scale each objective to 0-1 with 1 always best.

    Needed for weighted ranking, and the direction is applied here so no caller has
    to remember it. An objective where every candidate is identical normalises to 1
    throughout rather than dividing by zero.
    """
    matrix = np.array([objective_vector(s, objectives) for s in scores], dtype=float)
    out = np.zeros_like(matrix)
    for col, obj in enumerate(objectives):
        column = matrix[:, col]
        lo, hi = float(column.min()), float(column.max())
        if hi - lo < 1e-12:
            out[:, col] = 1.0
            continue
        scaled = (column - lo) / (hi - lo)
        out[:, col] = scaled if obj.maximise else 1.0 - scaled
    return out


def weighted_ranking(
    scores: Sequence[RotationScore],
    objectives: Sequence[Objective],
    weights: Mapping[str, float],
) -> tuple[tuple[int, float], ...]:
    """Rank by farmer-stated priorities, highest first.

    This is the only place a weighted sum appears, and it operates on simulated
    outcomes rather than on crop attributes. Weights are normalised so they express
    relative priority rather than absolute scale.
    """
    if not scores:
        return ()
    missing = [o.key for o in objectives if o.key not in weights]
    if missing:
        raise ParetoError(f"no weight given for {', '.join(missing)}")
    w = np.array([float(weights[o.key]) for o in objectives])
    if np.any(w < 0):
        raise ParetoError("weights cannot be negative")
    if w.sum() <= 0:
        raise ParetoError("at least one weight must be positive")
    w = w / w.sum()
    totals = normalise(scores, objectives) @ w
    order = np.argsort(-totals)
    return tuple((int(i), float(totals[i])) for i in order)


def weight_sensitivity(
    scores: Sequence[RotationScore],
    objectives: Sequence[Objective],
    weights: Mapping[str, float],
    *,
    perturbation: float = 0.5,
) -> dict[str, bool]:
    """Whether the top-ranked rotation survives shifting each weight.

    A recommendation that flips when one priority moves by half is not a
    recommendation, it is an artefact of the weighting - and the farmer should be
    told which of their stated priorities is actually deciding the answer.
    """
    base = weighted_ranking(scores, objectives, weights)
    if not base:
        return {}
    winner = base[0][0]
    stable: dict[str, bool] = {}
    for obj in objectives:
        flipped = False
        for factor in (1.0 - perturbation, 1.0 + perturbation):
            trial = dict(weights)
            trial[obj.key] = max(float(weights[obj.key]) * factor, 0.0)
            if sum(trial.values()) <= 0:
                continue
            if weighted_ranking(scores, objectives, trial)[0][0] != winner:
                flipped = True
                break
        stable[obj.key] = not flipped
    return stable
