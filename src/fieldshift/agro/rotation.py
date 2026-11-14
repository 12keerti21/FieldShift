"""Rotation simulation: the component that makes this a sequence problem.

Every model so far scores a single season. This one chains them, and the chaining
is the point. Four things carry from one crop to the next:

    soil water      a crop that empties the profile is a cost to its successor,
                    and a fallow that conserves water is a gift to it
    soil carbon     one continuous RothC run across the whole sequence
    soil nitrogen   straw locks it up, legumes leave a credit, fallow leaches it
    surface residue the previous crop's straw suppresses evaporation for the next
    crop history    return intervals only exist across a sequence

Carbon and nitrogen advance month by month alongside the season loop rather than
being aggregated afterwards, because nitrogen supply has to be known BEFORE a
yield can be computed and a yield has to be known before its residue can be
returned. Residue therefore enters the soil at harvest, not spread evenly across
the season - which is both causally correct and what actually happens.

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
from .nitrogen import (
    NitrogenFluxes,
    NitrogenState,
    fixation,
    initial_nitrogen_pools,
    nitrogen_step,
    nitrogen_stress_factor,
)
from .rothc import (
    DPM_RPM_ARABLE,
    CarbonFlows,
    RothCPools,
    RothCResult,
    initialise_from_measured_soc,
    rothc_step_detailed,
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
    fertiliser_n_kg_ha: float = 0.0
    year_offset: int = 0  # which year of the cycle this season sits in

    def __post_init__(self) -> None:
        if not 1 <= self.planting_doy <= 366:
            raise RotationError(f"planting_doy {self.planting_doy} out of range")
        if not 0.0 <= self.residue_removed_fraction <= 1.0:
            raise RotationError("residue_removed_fraction must be in [0, 1]")
        if self.year_offset < 0:
            raise RotationError("year_offset cannot be negative")
        if self.fertiliser_n_kg_ha < 0.0:
            raise RotationError("fertiliser cannot be negative")


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
    nitrogen: NitrogenState | None = None
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
    nitrogen_demand_kg_ha: float = 0.0
    nitrogen_supplied_kg_ha: float = 0.0
    nitrogen_fixed_kg_ha: float = 0.0
    nitrogen_factor: float = 1.0
    water_factor: float = 1.0
    limiting_factor: str = "none"

    @property
    def yield_loss_fraction(self) -> float | None:
        return None if self.relative_yield is None else 1.0 - self.relative_yield

    @property
    def nitrogen_limited(self) -> bool:
        return self.limiting_factor == "nitrogen"


@dataclass(frozen=True)
class RotationOutcome:
    """A full multi-cycle simulation of one rotation."""

    plan: RotationPlan
    seasons: tuple[SeasonOutcome, ...]
    carbon: RothCResult
    final_state: FieldState
    bare_days: int
    total_days: int
    nitrogen_fluxes: tuple[NitrogenFluxes, ...] = field(default_factory=tuple)
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
    def nitrogen_limited_seasons(self) -> int:
        """Seasons where nitrogen, not water, capped the yield.

        A rotation that is repeatedly nitrogen-limited is telling the farmer
        something different from one that is water-limited, and the two call for
        opposite responses.
        """
        return sum(1 for s in self.seasons if s.nitrogen_limited)

    @property
    def total_nitrogen_fixed_kg_ha(self) -> float:
        return sum(s.nitrogen_fixed_kg_ha for s in self.seasons)

    @property
    def total_nitrogen_leached_kg_ha(self) -> float:
        return sum(f.leached for f in self.nitrogen_fluxes)

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
            "nitrogen_fixed_kg_ha": self.total_nitrogen_fixed_kg_ha,
            "nitrogen_leached_kg_ha": self.total_nitrogen_leached_kg_ha,
            "nitrogen_limited_seasons": float(self.nitrogen_limited_seasons),
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
    initial_mineral_n_kg_ha: float = 25.0,
    bare_profile_depth_m: float = 1.5,
    initial_carbon_pools: RothCPools | None = None,
) -> RotationOutcome:
    """Simulate a rotation over several cycles of real weather.

    Gaps between crops are filled with explicit bare-fallow water balances, so the
    cost of an uncovered window - evaporation and runoff - and the water a dry
    fallow genuinely conserves are both accounted rather than assumed.

    ``initial_carbon_pools`` should be supplied when comparing rotations. Derived
    per-rotation, the spin-up would give each candidate a slightly different
    starting soil - because the pool split depends on the cover calendar - and the
    comparison would then measure part of that difference rather than the rotations.
    Sharing one starting state is both fairer and far faster, since the spin-up is
    hundreds of simulated years.
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

    if initial_carbon_pools is None:
        pools, implied_input = initialise_from_measured_soc(
            initial_soc_t_ha, clay_pct=soil.clay_pct, temp_c=clim_t, rain_mm=clim_r,
            et0_mm=clim_e, covered=cover_climatology,
        )
        notes.append(
            f"carbon pools initialised from {initial_soc_t_ha:.1f} t C/ha; implied "
            f"historic input {implied_input:.1f} t C/ha/yr"
        )
    else:
        pools = initial_carbon_pools
        notes.append(
            f"carbon pools supplied ({pools.total:.1f} t C/ha), shared across "
            "candidates so the comparison measures the rotations rather than "
            "differences in their spin-up"
        )

    # --- monthly axis for carbon and nitrogen ---
    sim_months = np.unique(month_keys)
    month_index = {m: i for i, m in enumerate(sim_months)}
    n_months = len(sim_months)
    m_temp = np.array([weather.tmean_c[month_keys == m].mean() for m in sim_months])
    m_rain = np.array([weather.precip_mm[month_keys == m].sum() for m in sim_months])
    m_et0 = np.array([et0_mm[month_keys == m].sum() for m in sim_months])
    m_carbon = np.zeros(n_months)
    m_irrigation = np.zeros(n_months)
    m_covered = np.zeros(n_months, dtype=bool)
    m_drainage = np.zeros(n_months)

    nitrogen = NitrogenState(
        initial_mineral_n_kg_ha, initial_nitrogen_pools(pools)
    )
    carbon_pools = pools
    soc_trace = [pools.total]
    co2_trace: list[float] = []
    tsmd_trace: list[float] = []
    rate_trace: list[float] = []
    fluxes: list[NitrogenFluxes] = []
    cursor_month = 0
    total_carbon_in = 0.0
    tsmd = 0.0

    def advance_to(target_month: int, residue_cn: float) -> float:
        """Step carbon and nitrogen forward, returning nitrogen taken up on the way."""
        nonlocal carbon_pools, nitrogen, cursor_month, tsmd, total_carbon_in
        uptake = 0.0
        while cursor_month < min(target_month, n_months):
            i = cursor_month
            before = carbon_pools
            carbon_pools, flows = rothc_step_detailed(
                carbon_pools, clay_pct=soil.clay_pct, temp_c=float(m_temp[i]),
                rain_mm=float(m_rain[i]), et0_mm=float(m_et0[i]),
                covered=bool(m_covered[i]), carbon_input_t_ha=float(m_carbon[i]),
                irrigation_mm=float(m_irrigation[i]),
                dpm_rpm_ratio=DPM_RPM_ARABLE, previous_tsmd_mm=tsmd,
            )
            tsmd = flows.tsmd_mm
            total_carbon_in += float(m_carbon[i])
            nitrogen, flux = nitrogen_step(
                nitrogen, flows, before, residue_cn=residue_cn,
                fixed_kg_n_ha=float(m_fixation[i]),
                fertiliser_kg_n_ha=float(m_fertiliser[i]),
                demand_kg_n_ha=float(m_demand[i]),
                deep_percolation_mm=float(m_drainage[i]),
                rootzone_water_mm=max(soil.taw_mm(1.0), 1.0),
                soil_is_wet=bool(m_drainage[i] > 5.0),
            )
            uptake += flux.taken_up
            fluxes.append(flux)
            soc_trace.append(carbon_pools.total)
            co2_trace.append(flows.co2)
            tsmd_trace.append(flows.tsmd_mm)
            rate_trace.append(flows.rate_modifier)
            cursor_month += 1
        return uptake

    m_demand = np.zeros(n_months)
    m_fixation = np.zeros(n_months)
    m_fertiliser = np.zeros(n_months)

    state = FieldState(
        root_depletion_frac=initial_root_depletion_frac,
        surface_depletion_frac=initial_surface_depletion_frac,
        carbon=pools,
        nitrogen=nitrogen,
        surface_residue_t_ha=initial_residue_t_ha,
    )
    outcomes: list[SeasonOutcome] = []
    first_day, last_day = slots[0][2], slots[-1][2] + slots[-1][3]
    bare_days = 0
    cursor = first_day
    last_decomposition = "medium"
    last_residue_cn = 60.0

    def run_window(crop: Crop, start: int, duration: int, season: SeasonPlan | None):
        policy = season.irrigation if season else RAINFED
        return run_water_balance(
            crop, soil, weather, et0_mm,
            planting_index=start, duration_days=duration, irrigation=policy,
            initial_root_depletion_frac=state.root_depletion_frac,
            initial_surface_depletion_frac=state.surface_depletion_frac,
            residue_cover_fraction=state.residue_cover_fraction,
            bare_profile_depth_m=bare_profile_depth_m,
        )

    def carry_water(result: WaterBalanceResult, start: int, duration: int) -> None:
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
        # Record the month-by-month water terms carbon and nitrogen need.
        for day in range(start, min(start + duration, len(weather))):
            i = month_index[month_keys[day]]
            m_irrigation[i] += float(result.irrigation_applied_mm[day - start])
            m_drainage[i] += float(result.deep_percolation_mm[day - start])

    for year, season, start, duration in slots:
        # Bare gap before this crop.
        if start > cursor:
            gap = start - cursor
            gap_result = run_window(fallow, cursor, gap, None)
            carry_water(gap_result, cursor, gap)
            for day in range(cursor, min(cursor + gap, len(weather))):
                m_covered[month_index[month_keys[day]]] = False
            advance_to(month_index[month_keys[min(start, len(weather) - 1)]],
                       last_residue_cn)
            state = replace(
                state,
                surface_residue_t_ha=decay_residue(
                    state.surface_residue_t_ha, last_decomposition, gap / 30.4
                ),
                nitrogen=nitrogen,
            )
            bare_days += gap
            outcomes.append(
                SeasonOutcome(
                    year=int(weather.year[cursor]), crop_id="fallow_bare",
                    crop_name=fallow.name, start_index=cursor, duration_days=gap,
                    water=gap_result,
                    residue_cover_at_sowing=state.residue_cover_fraction,
                    relative_yield=None, yield_t_ha=None, above_ground_dm_t_ha=0.0,
                    residue_returned_t_ha=0.0, carbon_input_t_ha=0.0, is_fallow=True,
                )
            )

        crop = registry.crop(season.crop_id)
        cover_at_sowing = state.residue_cover_fraction
        result = run_window(crop, start, duration, season)
        carry_water(result, start, duration)
        for day in range(start, min(start + duration, len(weather))):
            m_covered[month_index[month_keys[day]]] = True

        attainable = crop.economics.yield_t_ha[2]  # FAO-33 Ym, water-unlimited
        deficit = result.relative_transpiration_deficit
        relative_transpiration = max(1.0 - deficit, 0.0)

        # Nitrogen is settled on a SEASONAL balance, not month by month.
        #
        # Spreading demand across the months and capping uptake monthly rationed the
        # crop against whatever happened to be mineralised in each individual month,
        # discarding unmet demand instead of letting nitrogen accumulate. On a cold
        # winter-wheat season that starved the crop: 11 kg N/ha supplied against 117
        # demanded, an N factor of 0.10, and yields of 0.43 t/ha against an observed
        # 2.4. A crop takes nitrogen up when it is there, so nitrogen accumulates
        # through the season and uptake is settled at harvest.
        demand = crop.nitrogen.n_uptake_kg_per_t_product * attainable
        fixed_total = fixation(crop.nitrogen.bnf_mid, relative_transpiration)
        season_months = sorted({
            month_index[month_keys[d]]
            for d in range(start, min(start + duration, len(weather)))
        })
        if season_months and fixed_total > 0.0:
            for i in season_months:
                m_fixation[i] += fixed_total / len(season_months)
        # Fertiliser goes on at or near sowing, not averaged over the season.
        if season_months and season.fertiliser_n_kg_ha > 0.0:
            for i in season_months[: min(2, len(season_months))]:
                m_fertiliser[i] += season.fertiliser_n_kg_ha / min(2, len(season_months))

        harvest_month = season_months[-1] if season_months else cursor_month
        advance_to(harvest_month + 1, last_residue_cn)
        supplied = min(demand, nitrogen.mineral_n_kg_ha)
        nitrogen = replace(nitrogen, mineral_n_kg_ha=nitrogen.mineral_n_kg_ha - supplied)

        ky = crop.ky
        water_factor = 1.0 - (ky.relative_yield_loss(deficit) or 0.0)
        n_factor = nitrogen_stress_factor(supplied, demand)
        if ky.is_known:
            # Liebig: the binding constraint sets the ceiling. Multiplying would
            # charge the crop twice for the same lost growth.
            relative_yield = max(min(water_factor, n_factor), 0.0)
            yield_t_ha = relative_yield * attainable
            limiting = "nitrogen" if n_factor < water_factor - 1e-9 else "water"
            if min(water_factor, n_factor) > 0.999:
                limiting = "none"
        else:
            relative_yield = None
            yield_t_ha = None
            limiting = "none"

        dm = above_ground_dry_matter(crop, yield_t_ha, result.total_transpiration_mm)
        carbon, straw_left = carbon_input_from_crop(
            crop, dm, season.residue_removed_fraction
        )
        # Residue enters the month AFTER harvest. The harvest month itself has
        # already been stepped - advancing through it is what made the nitrogen
        # supply known - so writing carbon there would silently discard it. That
        # bug showed as a carbon change identical whether straw was baled or
        # retained, which is the symptom to watch for.
        residue_month = harvest_month + 1
        if 0 <= residue_month < n_months:
            m_carbon[residue_month] += carbon
        elif carbon > 0.0:
            notes.append(
                f"{crop.name} residue at the end of the record ({carbon:.2f} t C/ha) "
                "falls outside the simulated window and is not credited"
            )
        last_residue_cn = crop.residue.cn_ratio or 60.0
        last_decomposition = crop.residue.decomposition
        state = replace(state, surface_residue_t_ha=straw_left, nitrogen=nitrogen)

        outcomes.append(
            SeasonOutcome(
                year=year, crop_id=crop.id, crop_name=crop.name, start_index=start,
                duration_days=duration, water=result,
                residue_cover_at_sowing=cover_at_sowing,
                relative_yield=relative_yield, yield_t_ha=yield_t_ha,
                above_ground_dm_t_ha=dm, residue_returned_t_ha=straw_left,
                carbon_input_t_ha=carbon, is_fallow=False,
                nitrogen_demand_kg_ha=demand, nitrogen_supplied_kg_ha=supplied,
                nitrogen_fixed_kg_ha=fixed_total, nitrogen_factor=n_factor,
                water_factor=water_factor, limiting_factor=limiting,
            )
        )
        cursor = start + duration

    # Finish the carbon and nitrogen run to the end of the simulated window.
    end_month = month_index[month_keys[min(last_day, len(weather) - 1)]]
    advance_to(end_month + 1, last_residue_cn)

    soc = np.array(soc_trace)
    closure = float(
        (soc[-1] - soc[0]) - (total_carbon_in - float(np.sum(co2_trace)))
    )
    carbon_result = RothCResult(
        pools=(pools,),
        soc_t_ha=soc,
        co2_t_ha=np.array(co2_trace),
        tsmd_mm=np.array(tsmd_trace),
        rate_modifier=np.array(rate_trace),
        carbon_input_t_ha=m_carbon,
        closure_residual_t_ha=closure,
    )

    low_confidence = {s.crop_id for s in outcomes
                      if not s.is_fallow and registry.crop(s.crop_id).ky.confidence == "low"}
    if low_confidence:
        notes.append(
            "yield for " + ", ".join(sorted(low_confidence)) + " rests on a Ky taken "
            "from an analogue crop rather than FAO-33; treat those yields as "
            "indicative only"
        )
    n_limited = sum(1 for s in outcomes if s.nitrogen_limited)
    if n_limited:
        notes.append(
            f"{n_limited} season(s) were limited by nitrogen rather than water; "
            "that calls for a different response than a water constraint does"
        )

    return RotationOutcome(
        plan=plan,
        seasons=tuple(outcomes),
        carbon=carbon_result,
        final_state=replace(state, carbon=carbon_pools, nitrogen=nitrogen),
        bare_days=bare_days,
        total_days=last_day - first_day,
        nitrogen_fluxes=tuple(fluxes),
        notes=tuple(notes),
    )
