"""Daily soil water balance, FAO-56 dual crop coefficient (Chapters 7 and 8).

The dual coefficient splits crop water use into transpiration through the canopy
(Kcb) and evaporation straight off the soil surface (Ke). That split is the reason
this model is worth building rather than using a single Kc: transpiration is water
converted into crop growth, while soil evaporation is water simply lost. A bare or
sparsely covered field spends a large share of its water on the second, and
quantifying that share is what turns "cover the ground" from advice into a number.

Outputs are per-season. The rotation simulator chains seasons by passing the
closing depletion of one into the next, so stored soil water carries across the
rotation the way it does in a real field - a crop that leaves the profile dry is a
cost to whatever follows it, and the model has to see that.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..data.schema import Crop
from ..data.soils import SoilTexture
from .climate import DailyWeather
from .runoff import (
    cn_for_wetness,
    condition_for_crop,
    cover_type_for_crop,
    curve_number,
    scs_runoff_mm,
)

KC_MIN = 0.15  # FAO-56: minimum Kc for bare soil with the surface dry
DEFAULT_RH_MIN_PCT = 45.0  # the reference condition in FAO-56 eq 72
DEFAULT_WIND_MS = 2.0


class WaterBalanceError(ValueError):
    pass


@dataclass(frozen=True)
class IrrigationPolicy:
    """When and how much to irrigate.

    ``trigger_depletion_frac`` is a multiple of readily available water: 1.0 means
    irrigate exactly when the crop would otherwise begin to feel stress, below 1.0
    means irrigate earlier. ``efficiency`` is the fraction of applied water that
    reaches the root zone, so the reported requirement is at the field gate rather
    than at the root - the number a farmer actually has to find.

    ``management_depth_m`` is the profile depth scheduling is managed over, and it
    defaults to the crop's maximum rooting depth, which is the standard irrigation
    design assumption. It exists because triggering on the *instantaneous* root zone
    is badly wrong early in the season: three days after sowing, roots occupy ~15 cm
    holding ~20 mm, so any sensible depletion threshold is crossed immediately and
    the model irrigates a bare seedbed from day one. That produced ~190 mm of
    needless early irrigation and ~170 mm of soil evaporation in the Finney County
    run, roughly half the total overestimate. Real irrigators schedule against the
    design root zone and let the first pass fall in late June.

    ``mode="ponded"`` is a different model rather than a different setting, and it
    is dispatched to ``run_ponded_balance``. The ponded fields below apply only
    there and are ignored otherwise.
    """

    mode: str = "rainfed"  # "rainfed" | "refill_on_stress" | "ponded"
    trigger_depletion_frac: float = 1.0
    refill_to_depletion_frac: float = 0.0  # 0 = refill to field capacity
    max_application_mm: float = 60.0
    seasonal_cap_mm: float | None = None
    efficiency: float = 0.70
    wetted_fraction: float = 1.0
    management_depth_m: float | None = None

    # ---- ponded (paddy) only ----
    percolation_mm_day: float = 4.0
    establishment_mm: float = 150.0
    target_ponding_mm: float = 50.0
    min_ponding_mm: float = 20.0
    bund_height_mm: float = 100.0
    # When the permanent flood goes on and comes off, as days from sowing.
    #
    # Transplanted paddy goes into an already-flooded field, so the default of
    # day 0 to the end of the season is right for Punjab. Drill-seeded rice is
    # not: it is sown dry and flooded only at about the four-leaf stage, then
    # drained before harvest. Holding water for the whole season where the
    # farmer holds it for two thirds of one over-predicted Arkansas irrigation
    # by 77 percent against the observed record, which is what these two fields
    # exist to express.
    flood_start_day: int = 0
    flood_days: int | None = None  # None = until the end of the season

    def __post_init__(self) -> None:
        if self.mode not in ("rainfed", "refill_on_stress", "ponded"):
            raise WaterBalanceError(f"unknown irrigation mode {self.mode!r}")
        if self.mode == "ponded":
            if self.percolation_mm_day < 0.0:
                raise WaterBalanceError("percolation_mm_day cannot be negative")
            if self.establishment_mm < 0.0:
                raise WaterBalanceError("establishment_mm cannot be negative")
            if not 0.0 <= self.min_ponding_mm <= self.target_ponding_mm:
                raise WaterBalanceError(
                    "min_ponding_mm must be between 0 and target_ponding_mm"
                )
            if self.bund_height_mm < self.target_ponding_mm:
                raise WaterBalanceError(
                    "bund_height_mm must be at least target_ponding_mm, or the "
                    "field cannot hold the water it is told to apply"
                )
            if self.flood_start_day < 0:
                raise WaterBalanceError("flood_start_day cannot be negative")
            if self.flood_days is not None and self.flood_days <= 0:
                raise WaterBalanceError(
                    "flood_days must be positive when given; a paddy with no "
                    "flood is not a paddy, and should use a rainfed or "
                    "refill_on_stress policy instead"
                )
        if not 0.0 < self.efficiency <= 1.0:
            raise WaterBalanceError("irrigation efficiency must be in (0, 1]")
        if not 0.0 < self.wetted_fraction <= 1.0:
            raise WaterBalanceError("wetted fraction must be in (0, 1]")
        if self.trigger_depletion_frac <= 0.0:
            raise WaterBalanceError("trigger_depletion_frac must be positive")
        if self.management_depth_m is not None and self.management_depth_m <= 0.0:
            raise WaterBalanceError("management_depth_m must be positive when given")
        if not 0.0 <= self.refill_to_depletion_frac < 1.0:
            raise WaterBalanceError("refill_to_depletion_frac must be in [0, 1)")

    @property
    def is_irrigated(self) -> bool:
        return self.mode != "rainfed"


RAINFED = IrrigationPolicy(mode="rainfed")


@dataclass(frozen=True)
class WaterBalanceResult:
    """Daily series plus the season aggregates the rotation comparison needs.

    Timing convention, which matters when reading the series against each other:
    the coefficients ``ks``, ``ke`` and ``kcb`` are evaluated from the soil state at
    the START of each day, while ``root_depletion_mm`` and ``surface_depletion_mm``
    record the state at the END of the day, after that day's fluxes. So the last
    unstressed day can close with depletion already past readily available water -
    the stress it causes appears the following day. Comparing same-day ``ks`` with
    same-day depletion will therefore look inconsistent; lag the depletion by one
    day, or use ``initial_root_depletion_mm`` for the first day.
    """

    dates: np.ndarray
    crop_id: str
    et0_mm: np.ndarray
    kcb: np.ndarray
    ke: np.ndarray
    ks: np.ndarray
    canopy_fraction: np.ndarray
    transpiration_mm: np.ndarray
    potential_transpiration_mm: np.ndarray
    soil_evaporation_mm: np.ndarray
    root_depletion_mm: np.ndarray
    surface_depletion_mm: np.ndarray
    taw_mm: np.ndarray
    raw_mm: np.ndarray
    rainfall_mm: np.ndarray
    runoff_mm: np.ndarray
    infiltration_mm: np.ndarray
    irrigation_applied_mm: np.ndarray  # reaching the root zone
    irrigation_gross_mm: np.ndarray  # at the field gate, before efficiency losses
    deep_percolation_mm: np.ndarray
    curve_number: np.ndarray
    root_depth_m: np.ndarray
    initial_root_depletion_mm: float
    final_root_depletion_mm: float
    final_surface_depletion_mm: float
    root_growth_water_mm: float
    extraction_shortfall_mm: float
    closure_residual_mm: float
    policy: IrrigationPolicy
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __len__(self) -> int:
        return len(self.dates)

    # ---- totals ----

    @property
    def total_et0_mm(self) -> float:
        return float(self.et0_mm.sum())

    @property
    def total_transpiration_mm(self) -> float:
        return float(self.transpiration_mm.sum())

    @property
    def total_potential_transpiration_mm(self) -> float:
        return float(self.potential_transpiration_mm.sum())

    @property
    def total_soil_evaporation_mm(self) -> float:
        return float(self.soil_evaporation_mm.sum())

    @property
    def total_crop_water_use_mm(self) -> float:
        return self.total_transpiration_mm + self.total_soil_evaporation_mm

    @property
    def total_rainfall_mm(self) -> float:
        return float(self.rainfall_mm.sum())

    @property
    def total_runoff_mm(self) -> float:
        return float(self.runoff_mm.sum())

    @property
    def total_deep_percolation_mm(self) -> float:
        return float(self.deep_percolation_mm.sum())

    @property
    def total_irrigation_gross_mm(self) -> float:
        """Water the farmer must supply, including conveyance and application losses."""
        return float(self.irrigation_gross_mm.sum())

    # ---- decision-relevant indicators ----

    @property
    def evaporation_fraction(self) -> float:
        """Share of water use lost as bare-soil evaporation rather than transpired.

        The single most useful number this model produces for rotation design: it is
        the quantified cost of leaving ground uncovered, and it is invisible to a
        single-coefficient model.
        """
        total = self.total_crop_water_use_mm
        return float(self.total_soil_evaporation_mm / total) if total > 0 else 0.0

    @property
    def relative_transpiration_deficit(self) -> float:
        """1 - Ta/Tp over the season: the fraction of demand the crop could not meet.

        This is the quantity FAO-33 water production functions map onto yield loss
        through a crop-specific Ky. Ky is not yet in the crop table, so this is
        reported as a stress index and deliberately not converted into a yield
        number here.
        """
        tp = self.total_potential_transpiration_mm
        return float(1.0 - self.total_transpiration_mm / tp) if tp > 0 else 0.0

    @property
    def stressed(self) -> np.ndarray:
        return self.ks < 0.999

    @property
    def stress_days(self) -> int:
        return int(self.stressed.sum())

    @property
    def max_consecutive_stress_days(self) -> int:
        """A long unbroken spell does far more damage than the same days scattered."""
        best = run = 0
        for s in self.stressed:
            run = run + 1 if s else 0
            best = max(best, run)
        return best

    @property
    def runoff_fraction(self) -> float:
        return (
            float(self.total_runoff_mm / self.total_rainfall_mm)
            if self.total_rainfall_mm > 0
            else 0.0
        )

    def summary(self) -> dict[str, float]:
        return {
            "rainfall_mm": self.total_rainfall_mm,
            "et0_mm": self.total_et0_mm,
            "transpiration_mm": self.total_transpiration_mm,
            "soil_evaporation_mm": self.total_soil_evaporation_mm,
            "evaporation_fraction": self.evaporation_fraction,
            "irrigation_gross_mm": self.total_irrigation_gross_mm,
            "runoff_mm": self.total_runoff_mm,
            "runoff_fraction": self.runoff_fraction,
            "deep_percolation_mm": self.total_deep_percolation_mm,
            "relative_transpiration_deficit": self.relative_transpiration_deficit,
            "stress_days": float(self.stress_days),
            "max_consecutive_stress_days": float(self.max_consecutive_stress_days),
            "closure_residual_mm": self.closure_residual_mm,
        }


# ---------------------------------------------------------------- curves


def _stage_curve(crop: Crop, n_days: int, k) -> np.ndarray:
    """Interpolate a four-stage FAO-56 coefficient set over the season.

    Shared by the basal and single coefficient curves so the two cannot drift
    apart in shape - only in the values they are handed.
    """
    s = crop.stages_days
    kcb = np.empty(n_days)
    day = np.arange(n_days)

    ini_end = s.ini
    dev_end = s.ini + s.dev
    mid_end = s.ini + s.dev + s.mid
    season = max(s.total, 1)

    kcb[:] = k.end
    kcb[day < ini_end] = k.ini
    dev = (day >= ini_end) & (day < dev_end)
    if s.dev > 0:
        progress = (day[dev] - ini_end) / s.dev
        kcb[dev] = k.ini + progress * (k.mid - k.ini)
    kcb[(day >= dev_end) & (day < mid_end)] = k.mid
    late = (day >= mid_end) & (day < season)
    if s.late > 0:
        progress = (day[late] - mid_end) / s.late
        kcb[late] = k.mid + progress * (k.end - k.mid)
    return kcb


def kcb_curve(crop: Crop, n_days: int) -> np.ndarray:
    """Basal crop coefficient over the season, FAO-56 Table 17.

    Basal coefficients, not single ones: the dual model adds Ke for soil
    evaporation, so using Table 12 values here would double-count it. A test
    asserts the dual balance never reads ``crop.kc``, because this exact
    substitution was made once and inflated crop water use.
    """
    return _stage_curve(crop, n_days, crop.kcb)


def kc_curve(crop: Crop, n_days: int) -> np.ndarray:
    """Single crop coefficient over the season, FAO-56 Table 12.

    Used ONLY by the ponded paddy balance, where Table 12 is the correct table
    because its paddy values already include evaporation from the water surface.
    Every other path in this module must use ``kcb_curve``.
    """
    return _stage_curve(crop, n_days, crop.kc)


def root_depth_curve(crop: Crop, n_days: int, bare_profile_depth_m: float) -> np.ndarray:
    """Effective rooting depth over the season, m.

    Linear from the initial depth at planting to the maximum by the end of the
    development stage, which is where FAO-56 places full root extension, then
    constant. A bare fallow has no roots, so a fixed profile depth is tracked
    instead - otherwise there would be no reservoir in which to account for the
    water a fallow conserves.
    """
    if crop.is_fallow:
        return np.full(n_days, float(bare_profile_depth_m))

    s = crop.stages_days
    grow_end = max(s.ini + s.dev, 1)
    day = np.arange(n_days)
    progress = np.clip(day / grow_end, 0.0, 1.0)
    return crop.root.initial_depth_m + progress * (
        crop.root.max_depth_m - crop.root.initial_depth_m
    )


def _kc_max(kcb: float, u2: float, rh_min: float, height_m: float) -> float:
    """Upper limit on combined evaporation plus transpiration. FAO-56 eq 72."""
    adjustment = (0.04 * (u2 - 2.0) - 0.004 * (rh_min - 45.0)) * (
        max(height_m, 0.05) / 3.0
    ) ** 0.3
    return max(1.2 + adjustment, kcb + 0.05)


def _canopy_fraction(kcb: float, kcb_full: float, height_m: float, kc_min: float) -> float:
    """Fraction of ground covered by vegetation. FAO-56 eq 76."""
    span = kcb_full - kc_min
    if span <= 0.0 or kcb <= kc_min:
        return 0.0
    ratio = (kcb - kc_min) / span
    return float(np.clip(ratio ** (1.0 + 0.5 * max(height_m, 0.05)), 0.0, 0.99))


# ---------------------------------------------------------------- the balance


def run_water_balance(
    crop: Crop,
    texture: SoilTexture,
    weather: DailyWeather,
    et0_mm: np.ndarray,
    *,
    planting_index: int = 0,
    duration_days: int | None = None,
    initial_root_depletion_frac: float = 0.5,
    initial_surface_depletion_frac: float = 1.0,
    irrigation: IrrigationPolicy = RAINFED,
    subsoil_depletion_frac: float = 0.5,
    surface_layer_depth_m: float = 0.10,
    bare_profile_depth_m: float = 1.0,
    residue_cover_fraction: float = 0.0,
    kc_min: float = KC_MIN,
) -> WaterBalanceResult:
    """Run one crop occupancy and return the daily series plus season totals.

    ``residue_cover_fraction`` is the share of the soil surface covered by crop
    residue from the PREVIOUS crop. FAO-56 reduces soil evaporation by roughly 5
    percent for each 10 percent of surface under organic mulch, which this applies
    to Ke. It is not a detail: it is the mechanism by which one crop's residue saves
    water for the next, so it is a rotation benefit the model has to see rather than
    a calibration knob. Under Kansas no-till corn, 50-70 percent cover is typical.

    ``subsoil_depletion_frac`` is how depleted newly explored subsoil is assumed to
    be as roots deepen. It is a real simplification with a real effect - at 0 the
    crop finds every new layer at field capacity, at 1 it finds them at wilting
    point - so it is a named parameter rather than a buried constant, and it is one
    of the first things to check against local soil moisture observations.
    """
    # A ponded paddy is a different model, so it is dispatched rather than
    # approximated here. The dispatch lives inside this function on purpose: every
    # caller goes through run_water_balance, and a caller that had to remember to
    # check the mode itself would eventually forget and silently run a flooded
    # field through a drying-soil balance.
    if irrigation.mode == "ponded":
        return run_ponded_balance(
            crop, texture, weather, et0_mm,
            planting_index=planting_index,
            duration_days=duration_days,
            irrigation=irrigation,
            initial_root_depletion_frac=initial_root_depletion_frac,
            initial_surface_depletion_frac=initial_surface_depletion_frac,
            subsoil_depletion_frac=subsoil_depletion_frac,
            surface_layer_depth_m=surface_layer_depth_m,
            residue_cover_fraction=residue_cover_fraction,
            kc_min=kc_min,
        )

    if len(et0_mm) != len(weather):
        raise WaterBalanceError("et0 series length does not match the weather series")
    if not 0.0 <= subsoil_depletion_frac <= 1.0:
        raise WaterBalanceError("subsoil_depletion_frac must be in [0, 1]")
    if not 0.0 <= residue_cover_fraction <= 1.0:
        raise WaterBalanceError("residue_cover_fraction must be in [0, 1]")

    n = int(duration_days if duration_days is not None else crop.duration_days)
    if crop.is_fallow and duration_days is None:
        raise WaterBalanceError(
            "a bare fallow has no intrinsic duration; pass duration_days for the "
            "length of the bare window being costed"
        )
    if n <= 0:
        raise WaterBalanceError("season duration must be positive")
    if planting_index < 0 or planting_index + n > len(weather):
        raise WaterBalanceError(
            f"season of {n} days from index {planting_index} runs past the weather "
            f"series ({len(weather)} days)"
        )

    sl = slice(planting_index, planting_index + n)
    dates = weather.dates[sl]
    rain = weather.precip_mm[sl]
    et0 = np.asarray(et0_mm[sl], dtype=float)

    u2 = (
        weather.wind_2m_ms[sl]
        if weather.wind_2m_ms is not None
        else np.full(n, DEFAULT_WIND_MS)
    )
    if weather.rh_min_pct is not None:
        rh_min = weather.rh_min_pct[sl]
    elif weather.rh_mean_pct is not None:
        # FAO-56 eq 72 wants RHmin; mean humidity is the closest available proxy and
        # is biased high, which makes Kc_max slightly conservative.
        rh_min = weather.rh_mean_pct[sl]
    else:
        rh_min = np.full(n, DEFAULT_RH_MIN_PCT)

    notes: list[str] = []
    mulch_factor = 1.0 - 0.5 * residue_cover_fraction
    if residue_cover_fraction > 0.0:
        notes.append(
            f"soil evaporation reduced {(1.0 - mulch_factor):.0%} by "
            f"{residue_cover_fraction:.0%} residue cover (FAO-56 mulch adjustment)"
        )
    if weather.wind_2m_ms is None or weather.rh_min_pct is None:
        notes.append(
            "Kc_max used default wind and/or humidity (FAO-56 eq 72 reference "
            "conditions), so peak soil evaporation is approximate."
        )

    kcb_series = kcb_curve(crop, n) if not crop.is_fallow else np.zeros(n)
    zr_series = root_depth_curve(crop, n, bare_profile_depth_m)
    kcb_full = max(crop.kcb.mid, kc_min + 1e-6)
    height = crop.root.height_m

    taw_per_m = texture.taw_mm_per_m
    tew = texture.tew_mm(surface_layer_depth_m)
    rew = min(texture.rew_mm, tew)

    # Scheduling is managed over a fixed design profile, not the growing root zone.
    mgmt_depth = irrigation.management_depth_m or max(crop.root.max_depth_m, 0.1)
    taw_mgmt = taw_per_m * mgmt_depth
    raw_mgmt = max(crop.root.p_depletion * taw_mgmt, 1e-9)

    cover_type = cover_type_for_crop(crop)
    condition = condition_for_crop(crop)
    cn2 = curve_number(cover_type, texture.hydrologic_group, condition)

    # state
    taw = taw_per_m * zr_series[0]
    dr = float(np.clip(initial_root_depletion_frac, 0.0, 1.0)) * taw
    de = float(np.clip(initial_surface_depletion_frac, 0.0, 1.0)) * tew
    initial_dr = dr
    root_growth_total = 0.0
    shortfall_total = 0.0
    irrigation_season_gross = 0.0

    out = {
        k: np.zeros(n)
        for k in (
            "kcb", "ke", "ks", "fc", "t", "tp", "e", "dr", "de", "taw", "raw",
            "runoff", "infiltration", "irr_net", "irr_gross", "dp", "cn",
        )
    }

    for i in range(n):
        # 1. roots deepen into soil that carries its own depletion
        taw_new = taw_per_m * zr_series[i]
        if taw_new > taw:
            added = taw_new - taw
            dr += added * subsoil_depletion_frac
            root_growth_total += added * (1.0 - subsoil_depletion_frac)
        taw = taw_new
        raw = max(crop.root.p_depletion * taw, 1e-9)

        # 2. runoff, on a curve number that reflects how wet the profile already is
        relative_depletion = dr / taw if taw > 0 else 1.0
        cn = float(cn_for_wetness(cn2, relative_depletion))
        ro = float(scs_runoff_mm(rain[i], cn))
        infiltration = rain[i] - ro

        # 3. irrigation, decided on depletion over the MANAGEMENT depth at the
        #    start of the day. Soil below the current root zone but inside the
        #    managed profile is assumed to sit at `subsoil_depletion_frac` of its
        #    capacity, consistent with how root growth is accounted above.
        irr_net = irr_gross = 0.0
        depth_below = max(mgmt_depth - zr_series[i], 0.0)
        dr_managed = dr + taw_per_m * depth_below * subsoil_depletion_frac
        if (
            irrigation.is_irrigated
            and dr_managed >= irrigation.trigger_depletion_frac * raw_mgmt
        ):
            target = irrigation.refill_to_depletion_frac * taw_mgmt
            want = max(dr_managed - target, 0.0)
            # Never apply more than the current root zone can actually hold; the
            # surplus would otherwise be booked as deep percolation and lost, when
            # in reality it is banked in subsoil the roots reach later.
            irr_net = min(want, irrigation.max_application_mm, dr)
            irr_gross = irr_net / irrigation.efficiency
            if irrigation.seasonal_cap_mm is not None:
                remaining = irrigation.seasonal_cap_mm - irrigation_season_gross
                if remaining <= 0.0:
                    irr_net = irr_gross = 0.0
                elif irr_gross > remaining:
                    irr_gross = remaining
                    irr_net = irr_gross * irrigation.efficiency
            irrigation_season_gross += irr_gross

        # 4. evaporation and transpiration coefficients
        kcb = kcb_series[i]
        fc = _canopy_fraction(kcb, kcb_full, height, kc_min)
        kc_max = _kc_max(kcb, float(u2[i]), float(rh_min[i]), height)
        few = max(min(1.0 - fc, irrigation.wetted_fraction), 1e-6)
        kr = 1.0 if de <= rew else max((tew - de) / max(tew - rew, 1e-9), 0.0)
        kr = float(np.clip(kr, 0.0, 1.0))
        ke = min(kr * max(kc_max - kcb, 0.0), few * kc_max)
        # FAO-56: organic mulch suppresses soil evaporation by about 5% per 10% of
        # surface covered. This is how the previous crop's residue pays forward.
        ke *= mulch_factor

        ks = 1.0 if dr <= raw else max((taw - dr) / max(taw - raw, 1e-9), 0.0)
        ks = float(np.clip(ks, 0.0, 1.0))

        tp = kcb * et0[i]
        t_act = ks * tp
        e_act = ke * et0[i]

        # 5. root zone store
        available = taw - dr
        inflow = infiltration + irr_net
        after = available + inflow - (t_act + e_act)
        dp = max(after - taw, 0.0)
        after -= dp
        if after < 0.0:
            shortfall_total += -after
            after = 0.0
        dr = taw - after

        # 6. surface layer (FAO-56 eq 77, without the Tew term)
        de = float(
            np.clip(
                de - infiltration - irr_net / irrigation.wetted_fraction + e_act / few,
                0.0,
                tew,
            )
        )

        for key, value in (
            ("kcb", kcb), ("ke", ke), ("ks", ks), ("fc", fc), ("t", t_act),
            ("tp", tp), ("e", e_act), ("dr", dr), ("de", de), ("taw", taw),
            ("raw", raw), ("runoff", ro), ("infiltration", infiltration),
            ("irr_net", irr_net), ("irr_gross", irr_gross), ("dp", dp), ("cn", cn),
        ):
            out[key][i] = value

    # Mass balance must close exactly. Any residual is a bug, not an approximation,
    # so it is computed and surfaced rather than assumed.
    final_available = taw - dr
    expected = (
        (taw_per_m * zr_series[0] - initial_dr)
        + out["infiltration"].sum()
        + out["irr_net"].sum()
        + root_growth_total
        - out["t"].sum()
        - out["e"].sum()
        - out["dp"].sum()
        + shortfall_total
    )
    closure = float(final_available - expected)

    if shortfall_total > 1e-6:
        notes.append(
            f"{shortfall_total:.2f} mm of demand could not be met from the profile "
            "and was dropped; the stress coefficient should normally prevent this."
        )

    return WaterBalanceResult(
        dates=dates,
        crop_id=crop.id,
        et0_mm=et0,
        kcb=out["kcb"],
        ke=out["ke"],
        ks=out["ks"],
        canopy_fraction=out["fc"],
        transpiration_mm=out["t"],
        potential_transpiration_mm=out["tp"],
        soil_evaporation_mm=out["e"],
        root_depletion_mm=out["dr"],
        surface_depletion_mm=out["de"],
        taw_mm=out["taw"],
        raw_mm=out["raw"],
        rainfall_mm=rain,
        runoff_mm=out["runoff"],
        infiltration_mm=out["infiltration"],
        irrigation_applied_mm=out["irr_net"],
        irrigation_gross_mm=out["irr_gross"],
        deep_percolation_mm=out["dp"],
        curve_number=out["cn"],
        root_depth_m=zr_series,
        initial_root_depletion_mm=initial_dr,
        final_root_depletion_mm=dr,
        final_surface_depletion_mm=de,
        root_growth_water_mm=root_growth_total,
        extraction_shortfall_mm=shortfall_total,
        closure_residual_mm=closure,
        policy=irrigation,
        notes=tuple(notes),
    )


def run_ponded_balance(
    crop: Crop,
    texture: SoilTexture,
    weather: DailyWeather,
    et0_mm: np.ndarray,
    *,
    planting_index: int = 0,
    duration_days: int | None = None,
    irrigation: IrrigationPolicy,
    initial_root_depletion_frac: float = 0.5,
    initial_surface_depletion_frac: float = 1.0,
    subsoil_depletion_frac: float = 0.5,
    surface_layer_depth_m: float = 0.10,
    residue_cover_fraction: float = 0.0,
    kc_min: float = KC_MIN,
) -> WaterBalanceResult:
    """Water balance for a bunded paddy, flooded for part or all of its season.

    Rice is not one water regime. A transplanted crop goes into an already
    flooded field and stays wet to the drain, so one regime describes it. A
    drill-seeded crop is sown dry, grows for about five weeks on rain and flush
    irrigations, is permanently flooded at around the four-leaf stage, and is
    drained before harvest. Running the second as though it were the first
    over-predicted Arkansas irrigation by 77 percent against the observed
    record - the flood window is the single largest error the paddy model had.

    So this runs ONE crop through TWO regimes, switching on
    ``policy.flood_start_day`` and ``policy.flood_days``. The crop coefficient
    curve, the rooting curve and the soil state all run continuously across the
    switch; only the water physics changes:

        dry phase      the FAO-56 dual coefficient - transpiration through Kcb,
                       soil evaporation through Ke, water stress through Ks,
                       runoff through the curve number, irrigation on depletion
        flooded phase  standing water: no stress by construction, the single
                       Table 12 coefficient because it already includes open
                       water evaporation, percolation as a driven flux, and
                       rain impounded by the bund rather than shed

    Two handoffs matter and are explicit. At flood-up the profile is wetted to
    saturation before anything stands on the surface, and that water is charged
    as irrigation. At the drain the standing water leaves the field and the
    profile is handed back at field capacity, which is what the following crop
    actually inherits.

    This is a SEPARATE model from the dual-Kc balance above, not a configuration of
    it, because under standing water the dual-Kc machinery does not describe
    anything real:

      * Ke, Kr, TEW and REW parameterise a soil surface that wets and then dries.
        A ponded surface never dries, so the drying curve has no meaning and Kr is
        pinned at 1 by construction.
      * Ks exists to express water stress from root-zone depletion. A submerged
        field has no depletion to express, so Ks is 1 on every day by physics
        rather than by assumption.
      * Deep percolation in the dual-Kc balance is the overflow of a full profile.
        Under a puddled paddy it is a near-constant downward flux through the
        plough pan that continues whether or not the profile is "full", and it is
        typically the largest single term after ET.

    So the equations here are the ones used for paddy irrigation requirement:

        ETc        = Kc_single x ETo               (no stress, no separate Ke)
        pond       += rain + irrigation - ETc - percolation
        overflow    = pond above the bund height   -> runoff
        irrigation  = enough to restore the target depth when the pond runs low

    Note that it uses the SINGLE crop coefficient from FAO-56 Table 12, not the
    basal Kcb. That is the opposite of the rest of this module and it is correct
    here: Table 12's paddy Kc already includes evaporation from the water surface,
    which is precisely the term the dual model would otherwise compute from a
    drying-soil curve that does not apply. Using Kcb here would silently drop the
    open-water evaporation from a flooded field.

    Transpiration and evaporation are still reported separately so the rotation's
    evaporation-fraction metric stays comparable across crops. The split uses
    Kcb for transpiration and the Kc - Kcb remainder for evaporation off the water
    surface, which keeps T + E == ETc exactly.

    What this model does NOT claim:

      * It does not model yield loss from submergence depth, nitrogen volatilisation
        from floodwater, or the methane that is the dominant climate term for paddy.
        Methane matters enormously for a rice decision and is simply absent here;
        the carbon objective for a rice rotation is therefore incomplete in a
        direction that flatters rice.
      * ``percolation_mm_day`` is a single constant where the real flux depends on
        the puddled layer a farmer has or has not formed. It is the most
        load-bearing assumption in the whole paddy path, which is why it is a named
        policy field and reported in the notes.
    """
    if irrigation.mode != "ponded":
        raise WaterBalanceError(
            "run_ponded_balance needs a ponded policy; use run_water_balance instead"
        )
    if len(et0_mm) != len(weather):
        raise WaterBalanceError("et0 series length does not match the weather series")

    n = int(duration_days if duration_days is not None else crop.duration_days)
    if n <= 0:
        raise WaterBalanceError("season duration must be positive")
    if crop.is_fallow:
        raise WaterBalanceError("a bare fallow cannot be ponded")
    if planting_index < 0 or planting_index + n > len(weather):
        raise WaterBalanceError(
            f"season of {n} days from index {planting_index} runs past the weather "
            f"series ({len(weather)} days)"
        )

    sl = slice(planting_index, planting_index + n)
    dates = weather.dates[sl]
    rain = weather.precip_mm[sl]
    et0 = np.asarray(et0_mm[sl], dtype=float)

    kcb_series = kcb_curve(crop, n)
    kc_series = kc_curve(crop, n)
    # The single coefficient must not fall below the basal one; if the tables
    # disagree for this crop, trusting the larger keeps ETc >= transpiration.
    kc_series = np.maximum(kc_series, kcb_series)
    zr_series = root_depth_curve(crop, n, bare_profile_depth_m=1.0)

    taw_per_m = texture.taw_mm_per_m
    tew = texture.tew_mm(surface_layer_depth_m)
    rew = min(texture.rew_mm, tew)

    out = {
        k: np.zeros(n)
        for k in (
            "kcb", "ke", "ks", "fc", "t", "tp", "e", "dr", "de", "taw", "raw",
            "runoff", "infiltration", "irr_net", "irr_gross", "dp", "cn", "pond",
        )
    }

    # Puddling water wets and saturates the profile before anything stands on the
    # surface. Routing all of it into the pond made 80 mm of a 180 mm application
    # spill the bund on the first day, which is not what happens: a dry seedbed
    # takes that water in. So the profile is given an explicit store, filled
    # first, and it later buffers the crop when the pond runs low.
    mgmt_depth = irrigation.management_depth_m or max(crop.root.max_depth_m, 0.1)
    initial_deficit = (
        float(np.clip(initial_root_depletion_frac, 0.0, 1.0))
        * taw_per_m * mgmt_depth
    )
    # Going from field capacity to saturation is the drainable porosity, which is
    # the water a puddled layer holds above what a drained soil would.
    saturation_capacity = 1000.0 * max(texture.drainable_porosity, 0.0) * mgmt_depth
    profile_capacity = initial_deficit + saturation_capacity

    # Which days carry standing water.
    flood_from = min(int(irrigation.flood_start_day), n)
    flood_len = n - flood_from if irrigation.flood_days is None \
        else int(irrigation.flood_days)
    flood_to = min(flood_from + max(flood_len, 0), n)
    flooded_day = np.zeros(n, dtype=bool)
    flooded_day[flood_from:flood_to] = True
    if not flooded_day.any():
        raise WaterBalanceError(
            "the flood window falls entirely outside the season; a paddy with "
            "no flooded day should use a rainfed or refill_on_stress policy"
        )

    # Dry-phase state, used before flood-up and after the drain.
    tew = texture.tew_mm(surface_layer_depth_m)
    rew = min(texture.rew_mm, tew)
    kcb_full = max(crop.kcb.mid, kc_min + 1e-6)
    height = crop.root.height_m
    cn2 = curve_number(cover_type_for_crop(crop), texture.hydrologic_group,
                       condition_for_crop(crop))
    u2 = (weather.wind_2m_ms[sl] if weather.wind_2m_ms is not None
          else np.full(n, DEFAULT_WIND_MS))
    if weather.rh_min_pct is not None:
        rh_min = weather.rh_min_pct[sl]
    elif weather.rh_mean_pct is not None:
        rh_min = weather.rh_mean_pct[sl]
    else:
        rh_min = np.full(n, DEFAULT_RH_MIN_PCT)
    dr = float(np.clip(initial_root_depletion_frac, 0.0, 1.0)) * taw_per_m * zr_series[0]
    de = float(np.clip(initial_surface_depletion_frac, 0.0, 1.0)) * tew
    initial_dr = dr
    dry_shortfall = 0.0
    dry_irrigation_net = 0.0
    root_growth_total = 0.0
    taw_tracked = taw_per_m * zr_series[0]
    was_flooded = False
    drain_release = 0.0
    # The root-zone deficit the flood-up absorbs. Once the flood is on, that
    # deficit stops being tracked as depletion and becomes part of what the
    # profile store has to fill - so the closure must not count it twice.
    floodup_deficit = 0.0
    # The balance is tracked as it happens rather than reconstructed afterwards
    # from half a dozen accumulators. Reconstruction is where the errors live:
    # the drain release ended up subtracted on both sides of the equation, which
    # is invisible until a case with a drain is run.
    water_in = 0.0
    water_out = 0.0

    def storage_now() -> float:
        """Water the field is holding, on one datum across both regimes.

        It has to be the SAME datum either side of a flood-up or a drain, or the
        transition invents or destroys water. The datum is the wilting point of
        the current root zone, so:

            dry        taw - dr, exactly as the dual-Kc balance measures it
            flooded    the same, plus standing water, plus however far the
                       profile has been filled past the deficit the flood-up
                       absorbed

        Measuring against field capacity instead looked simpler and was wrong:
        field capacity moves as the roots deepen, so a profile that gained
        nothing appeared to gain the growth in available water.
        """
        if was_flooded:
            return taw_tracked - floodup_deficit + profile_store + pond
        return taw_tracked - dr

    storage_start = taw_per_m * zr_series[0] - dr

    pond = 0.0
    profile_store = 0.0
    irrigation_season_gross = 0.0
    establishment_applied = 0.0
    capped_days = 0
    soil_draw_total = 0.0
    dry_days = 0

    for i in range(n):
        taw = taw_per_m * zr_series[i]
        raw = max(crop.root.p_depletion * taw, 1e-9)
        kcb = float(kcb_series[i])
        kc = float(kc_series[i])
        is_flooded = bool(flooded_day[i])


        irr_net = 0.0
        ro = 0.0
        # Water let off at the drain leaves the field, but it is NOT rain that
        # failed to infiltrate - so it is added to the runoff series only after
        # infiltration has been computed. Folding it in earlier drove
        # infiltration negative by the whole pond depth.
        released = 0.0

        # ---- handoff: the flood goes on -------------------------------------
        if is_flooded and not was_flooded:
            # Wet the profile to saturation before anything stands on top. The
            # water this takes is real and is charged as irrigation; skipping it
            # is what made a 180 mm application spill the bund on day one.
            floodup_deficit += dr
            profile_capacity = dr + saturation_capacity
            profile_store = 0.0
            if irrigation.establishment_mm > 0.0:
                irr_net += irrigation.establishment_mm
                establishment_applied += irrigation.establishment_mm
            dr = 0.0
            de = 0.0

        # ---- handoff: the flood comes off -----------------------------------
        if was_flooded and not is_flooded:
            # Standing water is let off before harvest and the saturated excess
            # drains away with it; what the profile keeps is field capacity,
            # which is what the following crop actually inherits. The split is
            # explicit because "profile_store" above field capacity is water in
            # transit, not water the next crop can use.
            excess = max(profile_store - floodup_deficit, 0.0)
            released = pond + excess
            drain_release += released
            water_out += released
            pond = 0.0
            profile_store = 0.0
            dr = 0.0
            de = 0.0

        # Roots deepen into soil carrying its own depletion - but this is done
        # AFTER the transitions above, because both of them reset dr. Crediting
        # the growth first and then zeroing dr kept the credit and threw away
        # the depletion, which is a leak of exactly one day's growth.
        if taw > taw_tracked:
            added = taw - taw_tracked
            if is_flooded:
                # Under standing water the whole profile is wet, so roots
                # reaching deeper find soil already at field capacity or above.
                # The datum this balance measures against rises with the root
                # zone, and the water filling it is real - it came from the
                # pond. Not charging it left the balance short by the whole
                # growth in available water.
                root_growth_total += added
                water_in += added
            else:
                dr += added * subsoil_depletion_frac
                gained = added * (1.0 - subsoil_depletion_frac)
                root_growth_total += gained
                water_in += gained
        taw_tracked = taw

        if is_flooded:
            # ================= standing water =================================
            etc = kc * et0[i]
            t_act = kcb * et0[i]
            e_act = max(etc - t_act, 0.0)

            projected = pond + irr_net + rain[i] - etc - irrigation.percolation_mm_day
            if projected < irrigation.min_ponding_mm:
                want = irrigation.target_ponding_mm - projected
                applied = min(want, irrigation.max_application_mm)
                if applied < want:
                    capped_days += 1
                irr_net += max(applied, 0.0)

            irr_gross = irr_net / irrigation.efficiency
            if irrigation.seasonal_cap_mm is not None:
                remaining = irrigation.seasonal_cap_mm - irrigation_season_gross
                if remaining <= 0.0:
                    irr_net = irr_gross = 0.0
                elif irr_gross > remaining:
                    irr_gross = remaining
                    irr_net = irr_gross * irrigation.efficiency
            irrigation_season_gross += irr_gross

            inflow = irr_net + rain[i]
            to_profile = min(inflow, max(profile_capacity - profile_store, 0.0))
            profile_store += to_profile
            pond += inflow - to_profile
            spill = max(pond - irrigation.bund_height_mm, 0.0)
            pond -= spill
            ro += spill

            et_from_pond = min(pond, etc)
            pond -= et_from_pond
            et_from_profile = min(profile_store, etc - et_from_pond)
            profile_store -= et_from_profile
            soil_draw = etc - et_from_pond - et_from_profile
            soil_draw_total += soil_draw
            water_in += soil_draw
            if soil_draw > 0.0:
                dry_days += 1

            percolation = min(pond, irrigation.percolation_mm_day)
            pond -= percolation

            row = (("kcb", kcb), ("ke", max(kc - kcb, 0.0)), ("ks", 1.0),
                   ("fc", float(np.clip(kcb / kcb_full, 0.0, 1.0))),
                   ("t", t_act), ("tp", t_act), ("e", e_act),
                   ("dr", 0.0), ("de", 0.0), ("taw", taw), ("raw", raw),
                   ("runoff", ro), ("infiltration", rain[i] - ro),
                   ("irr_net", irr_net), ("irr_gross", irr_gross),
                   ("dp", percolation), ("cn", 0.0), ("pond", pond))
        else:
            # ================= dry phase, FAO-56 dual coefficient =============
            relative = dr / taw if taw > 0 else 1.0
            cn = float(cn_for_wetness(cn2, relative))
            ro += float(scs_runoff_mm(rain[i], cn))
            infiltration = rain[i] - ro

            # A flush, not a refill. Before the permanent flood a drill-seeded
            # crop is watered to wet the seedbed and keep the seedling going -
            # it is not scheduled against a deep design profile the way a pivot
            # is, and triggering on one asks for several times the water a
            # farmer actually runs. So the trigger and the amount both come
            # from the ACTUAL root zone, which is shallow this early.
            if dr >= irrigation.trigger_depletion_frac * raw:
                irr_net = min(dr, irrigation.max_application_mm)
            irr_gross = irr_net / irrigation.efficiency
            irrigation_season_gross += irr_gross
            dry_irrigation_net += irr_net

            fc = _canopy_fraction(kcb, kcb_full, height, kc_min)
            kc_max = _kc_max(kcb, float(u2[i]), float(rh_min[i]), height)
            few = max(min(1.0 - fc, irrigation.wetted_fraction), 1e-6)
            kr = 1.0 if de <= rew else max((tew - de) / max(tew - rew, 1e-9), 0.0)
            ke = min(float(np.clip(kr, 0.0, 1.0)) * max(kc_max - kcb, 0.0),
                     few * kc_max)
            ks = 1.0 if dr <= raw else max((taw - dr) / max(taw - raw, 1e-9), 0.0)
            ks = float(np.clip(ks, 0.0, 1.0))

            tp = kcb * et0[i]
            t_act = ks * tp
            e_act = ke * et0[i]

            available = taw - dr
            after = available + infiltration + irr_net - (t_act + e_act)
            percolation = max(after - taw, 0.0)
            after -= percolation
            if after < 0.0:
                dry_shortfall += -after
                water_in += -after
                after = 0.0
            dr = taw - after
            de = float(np.clip(
                de - infiltration - irr_net / irrigation.wetted_fraction + e_act / few,
                0.0, tew))

            row = (("kcb", kcb), ("ke", ke), ("ks", ks), ("fc", fc),
                   ("t", t_act), ("tp", tp), ("e", e_act),
                   ("dr", dr), ("de", de), ("taw", taw), ("raw", raw),
                   ("runoff", ro + released), ("infiltration", infiltration),
                   ("irr_net", irr_net), ("irr_gross", irr_gross),
                   ("dp", percolation), ("cn", cn), ("pond", 0.0))

        day = dict(row)
        water_in += day["irr_net"] + day["infiltration"]
        water_out += day["t"] + day["e"] + day["dp"]

        was_flooded = is_flooded
        for key, value in row:
            out[key][i] = value

    notes = [
        "Ponded paddy: a separate water balance from the dual-Kc model. The field "
        "is kept submerged, so there is no water stress by construction and yield "
        "here is not water-limited - the cost of rice shows up as irrigation, not "
        "as lost yield.",
        f"Percolation held at {irrigation.percolation_mm_day:.1f} mm/day through the "
        f"puddled layer, {out['dp'].sum():.0f} mm over the season. This single "
        "constant is the largest assumption in the paddy path.",
        f"Establishment (puddling and transplanting) {establishment_applied:.0f} mm, "
        f"of which up to {profile_capacity:.0f} mm wets and saturates the profile "
        "before any water stands on the surface.",
        "Methane emission from the flooded field is NOT modelled, and it is the "
        "dominant climate term for paddy. The carbon figure for a rice rotation is "
        "incomplete in rice's favour.",
    ]
    if capped_days:
        notes.append(
            f"On {capped_days} day(s) the pond could not be restored to target "
            f"because a single application is capped at "
            f"{irrigation.max_application_mm:.0f} mm."
        )

    if dry_days:
        notes.append(
            f"The pond fell to zero on {dry_days} day(s) and {soil_draw_total:.0f} mm "
            "of crop water use was met from the saturated profile instead of from "
            "standing water. On those days the field is not actually submerged, so "
            "the no-stress assumption is weakest there."
        )

    # Mass balance across BOTH regimes. Every drop is counted where it moves:
    # what entered, what left, and what the field is holding relative to field
    # capacity. A residual here is a bug rather than an approximation.
    closure = float((storage_now() - storage_start) - (water_in - water_out))

    return WaterBalanceResult(
        dates=dates,
        crop_id=crop.id,
        et0_mm=et0,
        kcb=out["kcb"],
        ke=out["ke"],
        ks=out["ks"],
        canopy_fraction=out["fc"],
        transpiration_mm=out["t"],
        potential_transpiration_mm=out["tp"],
        soil_evaporation_mm=out["e"],
        root_depletion_mm=out["dr"],
        surface_depletion_mm=out["de"],
        taw_mm=out["taw"],
        raw_mm=out["raw"],
        rainfall_mm=rain,
        runoff_mm=out["runoff"],
        infiltration_mm=out["infiltration"],
        irrigation_applied_mm=out["irr_net"],
        irrigation_gross_mm=out["irr_gross"],
        deep_percolation_mm=out["dp"],
        curve_number=out["cn"],
        root_depth_m=zr_series,
        initial_root_depletion_mm=initial_dr,
        # A paddy hands the next crop a profile at field capacity, which is the
        # real agronomic legacy of rice: wheat sown after rice starts wet.
        final_root_depletion_mm=dr,
        final_surface_depletion_mm=de,
        root_growth_water_mm=root_growth_total,
        extraction_shortfall_mm=dry_shortfall,
        closure_residual_mm=closure,
        policy=irrigation,
        notes=tuple(notes),
    )
