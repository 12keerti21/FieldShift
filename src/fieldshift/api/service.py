"""Turning precomputed bundles into answers, live.

Everything here is cheap. The expensive work - fetching weather and soil, simulating
every candidate through forty-one years - already happened when the bundle was
built. What remains is ranking a scored set against stated priorities and finding
which options are not beaten on every count, and that is milliseconds.

The division is deliberate: physics is precomputed because it does not depend on
anyone's preferences, and preferences are live because a farmer moving a slider
needs the answer to move with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from ..decide.objectives import Objective, RotationScore, select_objectives
from ..decide.pareto import (
    dominated_by,
    pareto_front,
    trade_off,
    weight_sensitivity,
    weighted_ranking,
)
from .bundle import OBJECTIVE_KEYS, load_bundle


class ServiceError(ValueError):
    pass


def _score_from_row(row: dict, objectives: Sequence[Objective]) -> RotationScore:
    """Rebuild the minimum RotationScore the ranking needs from a bundle row."""
    obj = row["objectives"]
    detail = row.get("detail", {})
    return RotationScore(
        name=row["name"],
        years=float(detail.get("years", 40.0)),
        production_t_ha_yr=float(obj.get("production", 0.0)),
        downside_production_t_ha_yr=float(obj.get("downside", 0.0)),
        irrigation_mm_yr=float(obj.get("irrigation", 0.0)),
        carbon_change_t_ha_yr=float(obj.get("carbon", 0.0)),
        water_erosion_t_ha_yr=float(obj.get("water_erosion", 0.0)),
        wind_exposure_index=float(obj.get("wind_exposure", 0.0)),
        bare_fraction=float(obj.get("bare_ground", 0.0)),
        evaporation_fraction=float(detail.get("evaporation_fraction", 0.0)),
        crops_per_decade=float(detail.get("crops_per_decade", 0.0)),
        notes=tuple(row.get("notes", ())),
    )


@dataclass(frozen=True)
class RankedSet:
    site: dict
    objectives: tuple[Objective, ...]
    rows: tuple[dict, ...]
    scores: tuple[RotationScore, ...]
    order: tuple[tuple[int, float], ...]
    front: tuple[int, ...]
    stability: dict[str, bool]


def rank_site(
    site_id: str,
    weights: dict[str, float],
    *,
    front_only: bool = True,
    bundle: dict | None = None,
) -> RankedSet:
    """Rank a site's rotations against stated priorities."""
    data = bundle or load_bundle(site_id)
    objectives = select_objectives(
        [o["key"] for o in data["objectives"] if o["key"] in OBJECTIVE_KEYS]
    )
    if not objectives:
        raise ServiceError(f"bundle for {site_id!r} carries no usable objectives")

    rows = list(data["rotations"])
    scores = [_score_from_row(r, objectives) for r in rows]
    front = pareto_front(scores, objectives)

    if front_only:
        # A dominated rotation is beaten on every count, so ranking it alongside the
        # front can only mislead - but the farmer's own rotation always stays in,
        # because they need to see where it sits even when it is dominated.
        keep = sorted(set(front) | {i for i, r in enumerate(rows) if r["is_existing"]})
        rows = [rows[i] for i in keep]
        scores = [scores[i] for i in keep]
        front = pareto_front(scores, objectives)

    order = weighted_ranking(scores, objectives, weights)
    stability = weight_sensitivity(scores, objectives, weights)
    return RankedSet(
        site=data["site"],
        objectives=objectives,
        rows=tuple(rows),
        scores=tuple(scores),
        order=order,
        front=front,
        stability=stability,
    )


def existing_index(ranked: RankedSet) -> int | None:
    for i, row in enumerate(ranked.rows):
        if row["is_existing"]:
            return i
    return None


def beaten_by(ranked: RankedSet) -> tuple[str, ...]:
    """Which rotations beat the farmer's current one on every objective.

    This is the strongest statement the tool can make, and also the one most worth
    being careful about - it only holds for the objectives actually being compared.
    """
    index = existing_index(ranked)
    if index is None:
        return ()
    return tuple(
        ranked.scores[j].name
        for j in dominated_by(ranked.scores, index, ranked.objectives)
    )


def trade_off_against_existing(ranked: RankedSet, target_index: int) -> dict:
    """What moving from the current rotation to another costs and gains."""
    index = existing_index(ranked)
    if index is None:
        raise ServiceError("this site has no recorded existing rotation to compare")
    if not 0 <= target_index < len(ranked.scores):
        raise ServiceError(f"no rotation at index {target_index}")
    result = trade_off(
        ranked.scores[index], ranked.scores[target_index], ranked.objectives
    )
    return {
        "from_name": result.from_name,
        "to_name": result.to_name,
        "better": [
            {
                "objective": o.key, "label": o.label, "units": o.units,
                "change": float(d), "better": True,
            }
            for o, d in result.gains
        ],
        "worse": [
            {
                "objective": o.key, "label": o.label, "units": o.units,
                "change": float(d), "better": False,
            }
            for o, d in result.losses
        ],
        "summary": result.describe(),
    }


def deciding_priorities(stability: dict[str, bool]) -> list[str]:
    """The priorities whose movement flips the top choice."""
    return sorted(k for k, stable in stability.items() if not stable)
