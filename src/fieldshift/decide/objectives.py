"""Turning a simulated rotation into comparable numbers.

Each objective declares its own direction and units, so nothing downstream has to
remember whether more is better. That sounds fussy until you have six objectives
where three are minimised, at which point a single sign error silently inverts a
recommendation.

Two deliberate choices about what gets measured.

**Downside, not average.** `downside_production` is the tenth percentile across the
simulated years, because a farmer does not farm the average year. A rotation with a
high mean and a catastrophic decile is not a good rotation, and a mean alone cannot
tell the difference.

That said, `downside` and `production` answer different questions and the
difference is easy to misread. This model simulates ONE FIELD through time, so a
fallow year in a wheat-fallow rotation genuinely produces nothing and the downside
is near zero. In practice that rotation is run across space as well as time - half
the farm in wheat and half in fallow every year - so farm-level income is far
steadier than a single field's. `production_t_ha_yr`, which averages over the whole
cycle, is the closer analogue of the farm-level view. Neither is wrong; presenting
only the field-level downside would unfairly condemn fallow rotations, and
presenting only the cycle average would hide that this particular field carries a
year of no income.

**Production, not margin.** Margin needs local prices. The crop table refuses to
invent them and `price_local_required` is set on every marketed crop, so the default
objective is marketable production in tonnes. Prices can be supplied, and when they
are the objective switches to margin and says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import numpy as np

from ..agro.rotation import RotationOutcome
from ..agro.rusle import WindExposure


class ObjectiveError(ValueError):
    pass


@dataclass(frozen=True)
class Objective:
    """One axis of comparison, with the direction baked in."""

    key: str
    label: str
    units: str
    maximise: bool
    extract: Callable[["RotationScore"], float]
    note: str = ""

    def better(self, a: float, b: float) -> bool:
        return a > b if self.maximise else a < b


@dataclass(frozen=True)
class RotationScore:
    """Everything the ranking needs from one simulated rotation."""

    name: str
    years: float
    production_t_ha_yr: float
    downside_production_t_ha_yr: float
    irrigation_mm_yr: float
    carbon_change_t_ha_yr: float
    water_erosion_t_ha_yr: float
    wind_exposure_index: float
    bare_fraction: float
    evaporation_fraction: float
    crops_per_decade: float
    margin_per_ha_yr: float | None = None
    currency: str | None = None
    notes: tuple[str, ...] = ()

    @property
    def has_margin(self) -> bool:
        return self.margin_per_ha_yr is not None


def score_rotation(
    outcome: RotationOutcome,
    *,
    water_erosion_t_ha_yr: float,
    wind: WindExposure | None = None,
    prices_per_tonne: Mapping[str, float] | None = None,
    variable_cost_per_ha_yr: Mapping[str, float] | None = None,
    currency: str | None = None,
    downside_percentile: float = 10.0,
) -> RotationScore:
    """Reduce a simulated rotation to a comparable score.

    ``prices_per_tonne`` is optional on purpose. Without it the objective is
    physical production; with it, margin. Supplying prices for only some crops is
    refused rather than silently valuing the rest at zero, which would make a
    diversified rotation look worse than it is.
    """
    years = outcome.total_days / 365.25
    if years <= 0:
        raise ObjectiveError("simulated period has no length")

    by_crop = outcome.yields_by_crop()
    total = float(sum(v.sum() for v in by_crop.values()))
    production = total / years  # already spans the whole window, fallow years included

    # Downside: the worst years of total annual production.
    #
    # Every year in the simulated window counts, including those that produced
    # nothing. Averaging only over cropped years would hand a fallow-heavy rotation
    # the yield of its good years and none of its empty ones - which is precisely
    # the comparison a farmer must not be shown, and it inflated a corn-corn-fallow
    # rotation's "bad year" to 8.8 t/ha before this was fixed.
    first = min(s.year for s in outcome.seasons)
    last = max(s.year for s in outcome.seasons)
    annual = {year: 0.0 for year in range(first, last + 1)}
    for season in outcome.seasons:
        if season.yield_t_ha is not None:
            annual[season.year] = annual.get(season.year, 0.0) + season.yield_t_ha
    series = np.array([annual[y] for y in sorted(annual)])
    downside = float(np.percentile(series, downside_percentile)) if series.size else 0.0

    margin = None
    notes: list[str] = []
    if prices_per_tonne is not None:
        missing = sorted(set(by_crop) - set(prices_per_tonne))
        if missing:
            raise ObjectiveError(
                "no price supplied for " + ", ".join(missing) + ". Valuing them at "
                "zero would penalise a diversified rotation for being diversified; "
                "supply every price or omit prices entirely."
            )
        revenue = sum(
            float(v.sum()) * prices_per_tonne[crop] for crop, v in by_crop.items()
        )
        costs = 0.0
        if variable_cost_per_ha_yr:
            seasons_by_crop: dict[str, int] = {}
            for season in outcome.seasons:
                if not season.is_fallow:
                    seasons_by_crop[season.crop_id] = (
                        seasons_by_crop.get(season.crop_id, 0) + 1
                    )
            costs = sum(
                variable_cost_per_ha_yr.get(crop, 0.0) * n
                for crop, n in seasons_by_crop.items()
            )
        margin = (revenue - costs) / years
        notes.append("margin uses supplied local prices")
    else:
        notes.append(
            "no local prices supplied, so the economic objective is physical "
            "production; margin requires market data this model will not invent"
        )

    crops = sum(1 for s in outcome.seasons if not s.is_fallow)
    return RotationScore(
        name=outcome.plan.name,
        years=years,
        production_t_ha_yr=production,
        downside_production_t_ha_yr=downside,
        irrigation_mm_yr=outcome.total_irrigation_gross_mm / years,
        carbon_change_t_ha_yr=outcome.carbon_change_per_year(years),
        water_erosion_t_ha_yr=water_erosion_t_ha_yr,
        wind_exposure_index=wind.exposure_index if wind else 0.0,
        bare_fraction=outcome.bare_fraction,
        evaporation_fraction=outcome.evaporation_fraction,
        crops_per_decade=10.0 * crops / years,
        margin_per_ha_yr=margin,
        currency=currency,
        notes=tuple(notes) + outcome.notes,
    )


OBJECTIVES: tuple[Objective, ...] = (
    Objective(
        "production", "marketable production", "t/ha/yr", True,
        lambda s: s.production_t_ha_yr,
        "physical output; becomes margin when local prices are supplied",
    ),
    Objective(
        "downside", "production in a bad year", "t/ha/yr", True,
        lambda s: s.downside_production_t_ha_yr,
        "tenth percentile across simulated years for THIS FIELD, including years it "
        "produces nothing. A farmer staggering the rotation across several fields "
        "sees a steadier farm-level income; compare with production for that view.",
    ),
    Objective(
        "irrigation", "irrigation demand", "mm/yr", False,
        lambda s: s.irrigation_mm_yr,
        "at the field gate, including application losses; the binding constraint "
        "where groundwater is declining",
    ),
    Objective(
        "carbon", "soil carbon change", "t C/ha/yr", True,
        lambda s: s.carbon_change_t_ha_yr,
        "RothC over the simulated period, 0-23 cm",
    ),
    Objective(
        "water_erosion", "water erosion", "t/ha/yr", False,
        lambda s: s.water_erosion_t_ha_yr,
        "RUSLE; near-zero on flat terrain and not a measure of total erosion",
    ),
    Objective(
        "wind_exposure", "wind erosion exposure", "index", False,
        lambda s: s.wind_exposure_index,
        "ordinal, for comparing rotations at one site; not a mass flux",
    ),
    Objective(
        "bare_ground", "bare ground", "fraction of time", False,
        lambda s: s.bare_fraction,
        "the clearest single explanation behind carbon loss and erosion exposure",
    ),
)

OBJECTIVES_BY_KEY = {o.key: o for o in OBJECTIVES}


def margin_objective(currency: str = "local") -> Objective:
    """The economic objective when prices are available."""
    return Objective(
        "margin", "gross margin", f"{currency}/ha/yr", True,
        lambda s: s.margin_per_ha_yr if s.margin_per_ha_yr is not None else 0.0,
        "revenue less supplied variable costs",
    )


def objective_vector(
    score: RotationScore, objectives: Sequence[Objective]
) -> np.ndarray:
    return np.array([o.extract(score) for o in objectives], dtype=float)


def select_objectives(keys: Sequence[str]) -> tuple[Objective, ...]:
    try:
        return tuple(OBJECTIVES_BY_KEY[k] for k in keys)
    except KeyError as exc:
        raise ObjectiveError(
            f"unknown objective {exc.args[0]!r}; have {sorted(OBJECTIVES_BY_KEY)}"
        ) from None
