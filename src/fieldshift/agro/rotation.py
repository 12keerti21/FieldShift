"""Rotation simulation: the component that makes this a sequence problem.

Every model so far scores a single season. This one chains them, and the chaining
is the point. Four things carry from one crop to the next:

    soil water      a crop that empties the profile is a cost to its successor,
                    and a fallow that conserves water is a gift to it
    soil carbon     one continuous RothC run across the whole sequence
    surface residue the previous crop's straw suppresses evaporation for the next
    crop history    return intervals only exist across a sequence

Without that carry-over, a rotation can only be scored crop by crop, which is
exactly the weighted-sum approach this project rejected. The test of whether the
chaining works is external and already measured: in Finney County, dryland wheat
after summer fallow out-yields continuous wheat by 10.8 percent over 25 observed
years. A simulator that cannot produce that is not simulating rotation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from ..data.registry import Registry
from ..data.schema import Crop
from ..data.soils import SoilTexture
from .climate import DailyWeather
from .rothc import (
    DPM_RPM_ARABLE,
    RothCPools,
    RothCResult,
    initialise_from_measured_soc,
    run_rothc,
)
from .waterbalance import (
    RAINFED,
    IrrigationPolicy,
    WaterBalanceResult,
    run_water_balance,
)

CARBON_FRACTION_OF_DM = 0.45

# Surface cover from residue mass. The exponential form is the standard relation
# used in erosion and tillage work; the coefficient here gives ~36% cover at
# 1 t DM/ha and ~74% at 3 t/ha, which matches reported small-grain straw cover.
RESIDUE_COVER_COEFFICIENT = 0.45

# First-order residue mass loss, per month, by the decomposition class already in
# crops.json. Cereal straw persists; legume residue disappears within a season.
RESIDUE_DECAY_PER_MONTH = {
    "fast": 0.22, "medium": 0.12, "slow": 0.06, "none": 0.0,
}

# Biomass water productivity, kg dry matter per m3 transpired. Used ONLY for crops
# with no marketable yield, where residue cannot be derived from a harvest. C4
# photosynthesis fixes more carbon per unit of water, so the two paths differ by
# roughly a factor of two. These are mid-range literature figures and are the
# weakest numbers in this module.
WATER_PRODUCTIVITY_KG_PER_M3 = {"C3": 1.8, "C4": 3.2}
C4_CROPS = frozenset({
    "maize", "sorghum", "pearl_millet", "finger_millet", "sugarcane",
    "sorghum_sudan_cover",
})


class RotationError(ValueError):
    pass


@dataclass(frozen=True)
class SeasonPlan:
    """One crop occupancy within a rotation cycle."""

    crop_id: str
    planting_doy: int
    duration_days: int | None = None
    irrigation: IrrigationPolicy = RAINFED
    residue_removed_fraction: float = 0.0
    year_offset: int = 0  # which year of the cycle this season sits in

    def __post_init__(self) -> None:
        if not 1 <= self.planting_doy <= 366:
            raise RotationError(f"planting_doy {self.planting_doy} out of range")
        if not 0.0 <= self.residue_removed_fraction <= 1.0:
            raise RotationError("residue_removed_fraction must be in [0, 1]")
        if self.year_offset < 0:
            raise RotationError("year_offset cannot be negative")


@dataclass(frozen=True)
class RotationPlan:
    """A repeating sequence of crop occupancies."""

    name: str
    seasons: tuple[SeasonPlan, ...]
    cycle_years: int
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.seasons:
            raise RotationError(f"{self.name}: a rotation needs at least one season")
        if self.cycle_years < 1:
            raise RotationError(f"{self.name}: cycle_years must be >= 1")
        if max(s.year_offset for s in self.seasons) >= self.cycle_years:
            raise RotationError(
                f"{self.name}: a season sits outside the declared {self.cycle_years}-"
                "year cycle"
            )

    @property
    def crop_sequence(self) -> tuple[str, ...]:
        """Crops in cycle order, for the return-interval check."""
        ordered = sorted(self.seasons, key=lambda s: (s.year_offset, s.planting_doy))
        return tuple(s.crop_id for s in ordered)


@dataclass(frozen=True)
class FieldState:
    """What one season hands to the next."""

    root_depletion_frac: float
    surface_depletion_frac: float
    carbon: RothCPools
    surface_residue_t_ha: float = 0.0

    @property
    def residue_cover_fraction(self) -> float:
        return residue_cover(self.surface_residue_t_ha)


@dataclass(frozen=True)
class SeasonOutcome:
    """One simulated crop occupancy."""

    year: int
    crop_id: str
    crop_name: str
    start_index: int
    duration_days: int
    water: WaterBalanceResult
    residue_cover_at_sowing: float
    relative_yield: float | None
    yield_t_ha: float | None
    above_ground_dm_t_ha: float
    residue_returned_t_ha: float
    carbon_input_t_ha: float
    is_fallow: bool

    @property
    def yield_loss_fraction(self) -> float | None:
        return None if self.relative_yield is None else 1.0 - self.relative_yield


@dataclass(frozen=True)
class RotationOutcome:
    """A full multi-cycle simulation of one rotation."""

    plan: RotationPlan
    seasons: tuple[SeasonOutcome, ...]
    carbon: RothCResult
    final_state: FieldState
    bare_days: int
    total_days: int
    notes: tuple[str, ...] = field(default_factory=tuple)

    # ---- water ----

    @property
    def total_irrigation_gross_mm(self) -> float:
        return sum(s.water.total_irrigation_gross_mm for s in self.seasons)

    @property
    def total_runoff_mm(self) -> float:
        return sum(s.water.total_runoff_mm for s in self.seasons)

    @property
    def total_soil_evaporation_mm(self) -> float:
        return sum(s.water.total_soil_evaporation_mm for s in self.seasons)

    @property
    def total_transpiration_mm(self) -> float:
        return sum(s.water.total_transpiration_mm for s in self.seasons)

    @property
    def evaporation_fraction(self) -> float:
        total = self.total_transpiration_mm + self.total_soil_evaporation_mm
        return self.total_soil_evaporation_mm / total if total > 0 else 0.0

    # ---- land occupancy ----

    @property
    def bare_fraction(self) -> float:
        """Share of the simulated period with no living crop.

        Reported because it is the single clearest explanatory variable behind both
        carbon loss and erosion exposure, and a farmer can act on it directly.
        """
        return self.bare_days / self.total_days if self.total_days else 0.0

    # ---- yield, per crop ----

    def yields_by_crop(self) -> dict[str, np.ndarray]:
        out: dict[str, list[float]] = {}
        for s in self.seasons:
            if s.yield_t_ha is not None:
                out.setdefault(s.crop_id, []).append(s.yield_t_ha)
        return {k: np.array(v) for k, v in out.items()}

    def mean_yield(self, crop_id: str) -> float:
        series = self.yields_by_crop().get(crop_id)
        if series is None or not len(series):
            raise RotationError(f"{crop_id} produced no yield in this rotation")
        return float(series.mean())

    def yield_percentile(self, crop_id: str, percentile: float) -> float:
        """A low percentile is the bad year - what the farmer actually plans around."""
        series = self.yields_by_crop().get(crop_id)
        if series is None or not len(series):
            raise RotationError(f"{crop_id} produced no yield in this rotation")
        return float(np.percentile(series, percentile))

    # ---- carbon ----

    @property
    def carbon_change_t_ha(self) -> float:
        return self.carbon.change_t_ha

    def carbon_change_per_year(self, years: float) -> float:
        return self.carbon.annual_change_t_ha(years)

    def summary(self, years: float) -> dict[str, float]:
        return {
            "years": years,
            "bare_fraction": self.bare_fraction,
            "transpiration_mm": self.total_transpiration_mm,
            "soil_evaporation_mm": self.total_soil_evaporation_mm,
            "evaporation_fraction": self.evaporation_fraction,
            "irrigation_gross_mm": self.total_irrigation_gross_mm,
            "runoff_mm": self.total_runoff_mm,
            "carbon_change_t_ha": self.carbon_change_t_ha,
            "carbon_change_t_ha_per_year": self.carbon_change_per_year(years),
        }


# ---------------------------------------------------------------- residue


def residue_cover(residue_t_ha: float) -> float:
    """Fraction of the soil surface covered by a given mass of residue."""
    return float(1.0 - np.exp(-RESIDUE_COVER_COEFFICIENT * max(residue_t_ha, 0.0)))


def above_ground_dry_matter(
    crop: Crop, yield_t_ha: float | None, transpiration_mm: float
) -> float:
    """Above-ground dry matter produced, t/ha.

    Two routes, because two different kinds of crop are involved. A harvested crop's
    residue follows from its yield through the product-to-residue ratio, which is
    well constrained. A green manure or cover crop has no harvest, so its biomass is
    estimated from transpiration through a water productivity figure - the weakest
    number in this module, and flagged as such.
    """
    if crop.is_fallow:
        return 0.0
    if yield_t_ha is not None and crop.economics.yield_typical > 0:
        return yield_t_ha * (1.0 + crop.residue.product_to_residue_ratio)
    pathway = "C4" if crop.id in C4_CROPS else "C3"
    wp = WATER_PRODUCTIVITY_KG_PER_M3[pathway]
    return wp * max(transpiration_mm, 0.0) * 10.0 / 1000.0


def carbon_input_from_crop(
    crop: Crop, above_ground_dm_t_ha: float, residue_removed_fraction: float
) -> tuple[float, float]:
    """Carbon returned to the soil, t C/ha, and the residue mass left on the surface.

    Roots are returned whatever happens to the straw, which is why baling reduces
    carbon input without eliminating it. For a green manure the whole above-ground
    mass is incorporated, so nothing can be removed.
    """
    removed = 0.0 if crop.economics.yield_typical == 0 else residue_removed_fraction
    harvested_fraction = (
        0.0 if crop.economics.yield_typical == 0
        else 1.0 / (1.0 + crop.residue.product_to_residue_ratio)
    )
    straw = above_ground_dm_t_ha * (1.0 - harvested_fraction)
    straw_returned = straw * (1.0 - removed)
    roots = above_ground_dm_t_ha * crop.residue.root_shoot_ratio
    carbon = (straw_returned + roots) * CARBON_FRACTION_OF_DM
    return carbon, straw_returned


def decay_residue(residue_t_ha: float, decomposition_class: str, months: float) -> float:
    """First-order residue mass loss over a period."""
    k = RESIDUE_DECAY_PER_MONTH.get(decomposition_class, 0.12)
    return float(residue_t_ha * np.exp(-k * max(months, 0.0)))


# ---------------------------------------------------------------- the simulation


def _occupancies(
    plan: RotationPlan, weather: DailyWeather, reg: Registry, start_year: int, cycles: int
) -> list[tuple[int, SeasonPlan, int, int]]:
    """Resolve the plan into concrete (year, season, start index, duration) slots."""
    slots: list[tuple[int, SeasonPlan, int, int]] = []
    for cycle in range(cycles):
        for season in sorted(plan.seasons, key=lambda s: (s.year_offset, s.planting_doy)):
            year = start_year + cycle * plan.cycle_years + season.year_offset
            matches = np.flatnonzero(
                (weather.year == year) & (weather.doy == season.planting_doy)
            )
            if not len(matches):
                continue
            crop = reg.crop(season.crop_id)
            duration = season.duration_days or crop.duration_days
            if duration <= 0:
                raise RotationError(
                    f"{plan.name}: {season.crop_id} has no duration; a bare fallow "
                    "slot must declare duration_days"
                )
            start = int(matches[0])
            if start + duration > len(weather):
                continue
            slots.append((year, season, start, duration))
    slots.sort(key=lambda s: s[2])

    # Reject overlapping occupancies rather than silently double-cropping a field.
    for a, b in zip(slots, slots[1:]):
        if a[2] + a[3] > b[2]:
            raise RotationError(
                f"{plan.name}: {a[1].crop_id} (day {a[2]}, {a[3]} d) overlaps "
                f"{b[1].crop_id} (day {b[2]}). Two crops cannot occupy one field."
            )
    return slots


def simulate_rotation(
    plan: RotationPlan,
    *,
    registry: Registry,
    soil: SoilTexture,
    weather: DailyWeather,
    et0_mm: np.ndarray,
    start_year: int,
    cycles: int,
    initial_soc_t_ha: float,
    initial_root_depletion_frac: float = 0.3,
    initial_surface_depletion_frac: float = 0.7,
    initial_residue_t_ha: float = 1.0,
    bare_profile_depth_m: float = 1.5,
) -> RotationOutcome:
    """Simulate a rotation over several cycles of real weather.

    Gaps between crops are filled with explicit bare-fallow water balances, so the
    cost of an uncovered window - evaporation and runoff - and the water a dry
    fallow genuinely conserves are both accounted rather than assumed.
    """
    if len(et0_mm) != len(weather):
        raise RotationError("et0 series length does not match the weather series")
    slots = _occupancies(plan, weather, registry, start_year, cycles)
    if not slots:
        raise RotationError(
            f"{plan.name}: no season fits inside the weather record from {start_year}"
        )

    fallow = registry.crop("fallow_bare")
    notes: list[str] = []

    # --- monthly climatology, for the carbon spin-up ---
    monthly = weather.monthly().complete()
    month_keys = weather.dates.astype("datetime64[M]")
    et0_monthly = np.array([et0_mm[month_keys == k].sum() for k in monthly.months])
    clim_t = np.array([monthly.tmean_c[monthly.month_number == m].mean()
                       for m in range(1, 13)])
    clim_r = np.array([monthly.precip_mm[monthly.month_number == m].mean()
                       for m in range(1, 13)])
    clim_e = np.array([et0_monthly[monthly.month_number == m].mean()
                       for m in range(1, 13)])

    occupied_months = {int(str(weather.dates[s][5:7])) if False else 0 for _, _, s, _ in []}
    cover_climatology = np.zeros(12, dtype=bool)
    for _, _, start, duration in slots:
        for m in np.unique(weather.month[start:start + duration]):
            cover_climatology[int(m) - 1] = True

    pools, implied_input = initialise_from_measured_soc(
        initial_soc_t_ha, clay_pct=soil.clay_pct, temp_c=clim_t, rain_mm=clim_r,
        et0_mm=clim_e, covered=cover_climatology,
    )
    notes.append(
        f"carbon pools initialised from {initial_soc_t_ha:.1f} t C/ha; implied "
        f"historic input {implied_input:.1f} t C/ha/yr"
    )

    # --- walk the timeline ---
    state = FieldState(
        root_depletion_frac=initial_root_depletion_frac,
        surface_depletion_frac=initial_surface_depletion_frac,
        carbon=pools,
        surface_residue_t_ha=initial_residue_t_ha,
    )
    outcomes: list[SeasonOutcome] = []
    first_day, last_day = slots[0][2], slots[-1][2] + slots[-1][3]
    daily_carbon = np.zeros(len(weather))
    daily_covered = np.zeros(len(weather), dtype=bool)
    daily_irrigation = np.zeros(len(weather))
    bare_days = 0
    cursor = first_day
    last_decomposition = "medium"

    def run_window(crop: Crop, start: int, duration: int, season: SeasonPlan | None):
        nonlocal state
        policy = season.irrigation if season else RAINFED
        return run_water_balance(
            crop, soil, weather, et0_mm,
            planting_index=start, duration_days=duration, irrigation=policy,
            initial_root_depletion_frac=state.root_depletion_frac,
            initial_surface_depletion_frac=state.surface_depletion_frac,
            residue_cover_fraction=state.residue_cover_fraction,
            bare_profile_depth_m=bare_profile_depth_m,
        )

    def advance(result: WaterBalanceResult, start: int, duration: int) -> None:
        nonlocal state
        taw_end = float(result.taw_mm[-1])
        tew = soil.tew_mm(0.10)
        state = replace(
            state,
            root_depletion_frac=float(np.clip(result.final_root_depletion_mm / taw_end, 0, 1))
            if taw_end > 0 else state.root_depletion_frac,
            surface_depletion_frac=float(np.clip(result.final_surface_depletion_mm / tew, 0, 1))
            if tew > 0 else state.surface_depletion_frac,
        )
        daily_irrigation[start:start + duration] = result.irrigation_applied_mm

    for year, season, start, duration in slots:
        # Bare gap before this crop.
        if start > cursor:
            gap = start - cursor
            gap_result = run_window(fallow, cursor, gap, None)
            advance(gap_result, cursor, gap)
            state = replace(
                state,
                surface_residue_t_ha=decay_residue(
                    state.surface_residue_t_ha, last_decomposition, gap / 30.4
                ),
            )
            bare_days += gap
            outcomes.append(
                SeasonOutcome(
                    year=int(weather.year[cursor]), crop_id="fallow_bare",
                    crop_name=fallow.name, start_index=cursor, duration_days=gap,
                    water=gap_result, residue_cover_at_sowing=state.residue_cover_fraction,
                    relative_yield=None, yield_t_ha=None, above_ground_dm_t_ha=0.0,
                    residue_returned_t_ha=0.0, carbon_input_t_ha=0.0, is_fallow=True,
                )
            )

        crop = registry.crop(season.crop_id)
        cover_at_sowing = state.residue_cover_fraction
        result = run_window(crop, start, duration, season)

        ky = crop.ky
        deficit = result.relative_transpiration_deficit
        loss = ky.relative_yield_loss(deficit)
        relative_yield = None if loss is None else 1.0 - loss
        attainable = crop.economics.yield_t_ha[2]  # water-unlimited potential, FAO-33 Ym
        yield_t_ha = None if relative_yield is None else relative_yield * attainable

        dm = above_ground_dry_matter(crop, yield_t_ha, result.total_transpiration_mm)
        carbon, straw_left = carbon_input_from_crop(
            crop, dm, season.residue_removed_fraction
        )

        daily_carbon[start:start + duration] = carbon / duration
        daily_covered[start:start + duration] = True
        advance(result, start, duration)
        state = replace(state, surface_residue_t_ha=straw_left)
        last_decomposition = crop.residue.decomposition

        outcomes.append(
            SeasonOutcome(
                year=year, crop_id=crop.id, crop_name=crop.name, start_index=start,
                duration_days=duration, water=result,
                residue_cover_at_sowing=cover_at_sowing,
                relative_yield=relative_yield, yield_t_ha=yield_t_ha,
                above_ground_dm_t_ha=dm, residue_returned_t_ha=straw_left,
                carbon_input_t_ha=carbon, is_fallow=False,
            )
        )
        cursor = start + duration

    # --- one continuous carbon run over the simulated window ---
    window = slice(first_day, last_day)
    sim_keys = month_keys[window]
    sim_months = np.unique(sim_keys)
    carbon_monthly = np.array([daily_carbon[window][sim_keys == k].sum() for k in sim_months])
    irrig_monthly = np.array([daily_irrigation[window][sim_keys == k].sum() for k in sim_months])
    covered_monthly = np.array(
        [daily_covered[window][sim_keys == k].mean() > 0.5 for k in sim_months]
    )
    temp_monthly = np.array([weather.tmean_c[window][sim_keys == k].mean() for k in sim_months])
    rain_monthly = np.array([weather.precip_mm[window][sim_keys == k].sum() for k in sim_months])
    et0_sim_monthly = np.array([et0_mm[window][sim_keys == k].sum() for k in sim_months])

    carbon_result = run_rothc(
        pools, clay_pct=soil.clay_pct, temp_c=temp_monthly, rain_mm=rain_monthly,
        et0_mm=et0_sim_monthly, covered=covered_monthly,
        carbon_input_t_ha=carbon_monthly, irrigation_mm=irrig_monthly,
        dpm_rpm_ratio=DPM_RPM_ARABLE,
    )

    low_confidence = {s.crop_id for s in outcomes
                      if not s.is_fallow and registry.crop(s.crop_id).ky.confidence == "low"}
    if low_confidence:
        notes.append(
            "yield for " + ", ".join(sorted(low_confidence)) + " rests on a Ky taken "
            "from an analogue crop rather than FAO-33; treat those yields as "
            "indicative only"
        )

    return RotationOutcome(
        plan=plan,
        seasons=tuple(outcomes),
        carbon=carbon_result,
        final_state=replace(state, carbon=carbon_result.pools[-1]),
        bare_days=bare_days,
        total_days=last_day - first_day,
        notes=tuple(notes),
    )
