"""Searching over rotations: generate, simulate, score, find the trade-offs.

This is the step that makes FieldShift a decision-support tool rather than a
calculator. Everything before it evaluates a rotation someone chose; this proposes
them, discards the ones that cannot work, simulates the rest through four decades of
observed weather, and returns the set where no option is beaten on every count.

All candidates share one starting soil state. Derived per rotation, the carbon
spin-up would hand each candidate a slightly different soil - the pool split depends
on the cover calendar - and the comparison would then partly measure that rather
than the rotations themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from ..agro.climate import DailyWeather
from ..agro.rotation import RotationOutcome, RotationPlan, simulate_rotation
from ..agro.rothc import RothCPools, initialise_from_measured_soc
from ..agro.rusle import WindExposure, soil_loss, wind_exposure_index
from ..data.registry import Registry
from ..data.soils import SoilTexture
from .candidates import CandidateSet, RotationConstraints, YearPattern, enumerate_rotations
from .objectives import (
    OBJECTIVES,
    Objective,
    RotationScore,
    score_rotation,
)
from .pareto import pareto_front, trade_off


class SearchError(ValueError):
    pass


@dataclass(frozen=True)
class SearchResult:
    """Every candidate, its score, and the non-dominated set."""

    candidates: CandidateSet
    plans: tuple[RotationPlan, ...]
    outcomes: tuple[RotationOutcome, ...]
    scores: tuple[RotationScore, ...]
    objectives: tuple[Objective, ...]
    front: tuple[int, ...]
    failures: tuple[tuple[str, str], ...] = ()

    def __len__(self) -> int:
        return len(self.scores)

    @property
    def front_scores(self) -> tuple[RotationScore, ...]:
        return tuple(self.scores[i] for i in self.front)

    def best_for(self, objective_key: str) -> RotationScore:
        """The single best rotation on one axis, ignoring every other."""
        objective = next((o for o in self.objectives if o.key == objective_key), None)
        if objective is None:
            raise SearchError(f"{objective_key!r} is not among the search objectives")
        if not self.scores:
            raise SearchError("nothing was scored")
        values = [objective.extract(s) for s in self.scores]
        index = int(np.argmax(values) if objective.maximise else np.argmin(values))
        return self.scores[index]

    def trade_offs_from(self, baseline_name: str) -> tuple:
        """What each front member costs and gains relative to a baseline rotation."""
        base = next((s for s in self.scores if s.name == baseline_name), None)
        if base is None:
            raise SearchError(f"no scored rotation named {baseline_name!r}")
        return tuple(
            trade_off(base, s, self.objectives)
            for s in self.front_scores if s.name != baseline_name
        )

    def describe(self, limit: int = 10) -> str:
        lines = [self.candidates.describe()]
        if self.failures:
            lines.append(f"  {len(self.failures)} candidates failed to simulate")
        lines.append(
            f"\n{len(self.front)} rotations on the Pareto front of "
            f"{len(self.scores)} simulated"
        )
        header = f"  {'rotation':46s}" + "".join(
            f" {o.key[:11]:>12s}" for o in self.objectives
        )
        lines.append(header)
        for score in self.front_scores[:limit]:
            row = f"  {score.name[:46]:46s}"
            for o in self.objectives:
                row += f" {o.extract(score):12.3f}"
            lines.append(row)
        return "\n".join(lines)


def daily_ground_cover(outcome: RotationOutcome, n_days: int) -> np.ndarray:
    """Ground cover fraction per day across the simulated window.

    Takes the greater of living canopy and surface residue, since either protects
    the soil surface. Residue is held at its sowing value through a season, which
    understates early-season cover slightly and overstates late-season cover - a
    simplification worth naming because it feeds the wind exposure index.
    """
    cover = np.zeros(n_days)
    for season in outcome.seasons:
        start, duration = season.start_index, season.duration_days
        canopy = season.water.canopy_fraction
        residue = season.residue_cover_at_sowing
        end = min(start + duration, n_days)
        if end <= start:
            continue
        cover[start:end] = np.maximum(canopy[: end - start], residue)
    return cover


def evaluate_plan(
    plan: RotationPlan,
    *,
    registry: Registry,
    soil: SoilTexture,
    weather: DailyWeather,
    et0_mm: np.ndarray,
    start_year: int,
    cycles: int,
    initial_soc_t_ha: float,
    initial_carbon_pools: RothCPools,
    monthly_precip_mm: np.ndarray,
    ls: float,
    practice: str = "up_and_down_slope",
    prices_per_tonne: Mapping[str, float] | None = None,
    variable_cost_per_ha_yr: Mapping[str, float] | None = None,
    currency: str | None = None,
) -> tuple[RotationOutcome, RotationScore, WindExposure]:
    """Simulate one rotation and reduce it to a score."""
    outcome = simulate_rotation(
        plan, registry=registry, soil=soil, weather=weather, et0_mm=et0_mm,
        start_year=start_year, cycles=cycles, initial_soc_t_ha=initial_soc_t_ha,
        initial_carbon_pools=initial_carbon_pools,
    )
    cover = daily_ground_cover(outcome, len(weather))
    window = slice(
        outcome.seasons[0].start_index,
        outcome.seasons[-1].start_index + outcome.seasons[-1].duration_days,
    )
    wind = (
        wind_exposure_index(weather.wind_2m_ms[window], cover[window])
        if weather.wind_2m_ms is not None
        else None
    )

    # Time-weighted mean cover drives the RUSLE C factor over the whole rotation.
    mean_residue = float(np.mean(cover[window]))
    factors = soil_loss(
        monthly_precip_mm=monthly_precip_mm,
        sand_pct=soil.sand_pct, silt_pct=soil.silt_pct, clay_pct=soil.clay_pct,
        organic_carbon_pct=soil.organic_carbon_pct or 1.0,
        ls=ls, canopy_cover_fraction=0.0, residue_cover_fraction=mean_residue,
        practice=practice,
    )
    score = score_rotation(
        outcome,
        water_erosion_t_ha_yr=factors.soil_loss_t_ha_yr,
        wind=wind,
        prices_per_tonne=prices_per_tonne,
        variable_cost_per_ha_yr=variable_cost_per_ha_yr,
        currency=currency,
    )
    return outcome, score, wind


def search_rotations(
    patterns: Sequence[YearPattern],
    *,
    registry: Registry,
    soil: SoilTexture,
    weather: DailyWeather,
    et0_mm: np.ndarray,
    start_year: int,
    initial_soc_t_ha: float,
    ls: float,
    cycle_years: int = 4,
    constraints: RotationConstraints | None = None,
    objectives: Sequence[Objective] = OBJECTIVES,
    max_candidates: int | None = 400,
    practice: str = "up_and_down_slope",
    prices_per_tonne: Mapping[str, float] | None = None,
    variable_cost_per_ha_yr: Mapping[str, float] | None = None,
    currency: str | None = None,
    extra_plans: Sequence[RotationPlan] = (),
) -> SearchResult:
    """Enumerate, simulate and rank rotations over the observed record.

    ``extra_plans`` is for rotations that must appear whatever the search finds -
    in practice the farmer's existing rotation, which has to be in the comparison
    even if the generator would not have proposed it.
    """
    if len(et0_mm) != len(weather):
        raise SearchError("et0 series length does not match the weather series")
    if not objectives:
        raise SearchError("at least one objective is required")

    candidates = enumerate_rotations(
        patterns, registry=registry, cycle_years=cycle_years,
        constraints=constraints, max_candidates=max_candidates,
    )
    plans = list(candidates.plans) + list(extra_plans)
    if not plans:
        raise SearchError(
            "no rotation survived the constraints. " + candidates.describe()
        )

    # One shared starting soil, computed once.
    monthly = weather.monthly().complete()
    keys = weather.dates.astype("datetime64[M]")
    et0_monthly = np.array([et0_mm[keys == k].sum() for k in monthly.months])
    clim_t = np.array([monthly.tmean_c[monthly.month_number == m].mean()
                       for m in range(1, 13)])
    clim_r = np.array([monthly.precip_mm[monthly.month_number == m].mean()
                       for m in range(1, 13)])
    clim_e = np.array([et0_monthly[monthly.month_number == m].mean()
                       for m in range(1, 13)])
    shared_pools, _ = initialise_from_measured_soc(
        initial_soc_t_ha, clay_pct=soil.clay_pct, temp_c=clim_t, rain_mm=clim_r,
        et0_mm=clim_e, covered=np.array([True] * 8 + [False] * 4),
    )
    monthly_precip_climatology = clim_r

    years_available = int(weather.year[-1] - weather.year[0])
    outcomes, scores, kept, failures = [], [], [], []
    for plan in plans:
        cycles = max(1, years_available // plan.cycle_years)
        try:
            outcome, score, _ = evaluate_plan(
                plan, registry=registry, soil=soil, weather=weather, et0_mm=et0_mm,
                start_year=start_year, cycles=cycles,
                initial_soc_t_ha=initial_soc_t_ha,
                initial_carbon_pools=shared_pools,
                monthly_precip_mm=monthly_precip_climatology, ls=ls,
                practice=practice, prices_per_tonne=prices_per_tonne,
                variable_cost_per_ha_yr=variable_cost_per_ha_yr, currency=currency,
            )
        except Exception as exc:  # a candidate that cannot be simulated is reported
            failures.append((plan.name, f"{type(exc).__name__}: {exc}"))
            continue
        outcomes.append(outcome)
        scores.append(score)
        kept.append(plan)

    if not scores:
        raise SearchError(
            "every candidate failed to simulate; first error: "
            + (failures[0][1] if failures else "unknown")
        )

    front = pareto_front(scores, objectives)
    return SearchResult(
        candidates=candidates,
        plans=tuple(kept),
        outcomes=tuple(outcomes),
        scores=tuple(scores),
        objectives=tuple(objectives),
        front=front,
        failures=tuple(failures),
    )
