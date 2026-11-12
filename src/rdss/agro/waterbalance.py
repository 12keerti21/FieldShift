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
    """

    mode: str = "rainfed"  # "rainfed" | "refill_on_stress"
    trigger_depletion_frac: float = 1.0
    refill_to_depletion_frac: float = 0.0  # 0 = refill to field capacity
    max_application_mm: float = 60.0
    seasonal_cap_mm: float | None = None
    efficiency: float = 0.70
    wetted_fraction: float = 1.0

    def __post_init__(self) -> None:
        if self.mode not in ("rainfed", "refill_on_stress"):
            raise WaterBalanceError(f"unknown irrigation mode {self.mode!r}")
        if not 0.0 < self.efficiency <= 1.0:
            raise WaterBalanceError("irrigation efficiency must be in (0, 1]")
        if not 0.0 < self.wetted_fraction <= 1.0:
            raise WaterBalanceError("wetted fraction must be in (0, 1]")
        if self.trigger_depletion_frac <= 0.0:
            raise WaterBalanceError("trigger_depletion_frac must be positive")
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


def kcb_curve(crop: Crop, n_days: int) -> np.ndarray:
    """Basal crop coefficient over the season, FAO-56 four-stage form.

    Constant at Kcb_ini through emergence, rising linearly through development,
    flat at Kcb_mid through mid-season, falling linearly to Kcb_end at harvest.
    """
    s = crop.stages_days
    kcb = np.empty(n_days)
    day = np.arange(n_days)

    ini_end = s.ini
    dev_end = s.ini + s.dev
    mid_end = s.ini + s.dev + s.mid
    season = max(s.total, 1)

    kcb[:] = crop.kc.end
    kcb[day < ini_end] = crop.kc.ini
    dev = (day >= ini_end) & (day < dev_end)
    if s.dev > 0:
        progress = (day[dev] - ini_end) / s.dev
        kcb[dev] = crop.kc.ini + progress * (crop.kc.mid - crop.kc.ini)
    kcb[(day >= dev_end) & (day < mid_end)] = crop.kc.mid
    late = (day >= mid_end) & (day < season)
    if s.late > 0:
        progress = (day[late] - mid_end) / s.late
        kcb[late] = crop.kc.mid + progress * (crop.kc.end - crop.kc.mid)
    return kcb


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
    kc_min: float = KC_MIN,
) -> WaterBalanceResult:
    """Run one crop occupancy and return the daily series plus season totals.

    ``subsoil_depletion_frac`` is how depleted newly explored subsoil is assumed to
    be as roots deepen. It is a real simplification with a real effect - at 0 the
    crop finds every new layer at field capacity, at 1 it finds them at wilting
    point - so it is a named parameter rather than a buried constant, and it is one
    of the first things to check against local soil moisture observations.
    """
    if len(et0_mm) != len(weather):
        raise WaterBalanceError("et0 series length does not match the weather series")
    if not 0.0 <= subsoil_depletion_frac <= 1.0:
        raise WaterBalanceError("subsoil_depletion_frac must be in [0, 1]")

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
    if weather.wind_2m_ms is None or weather.rh_min_pct is None:
        notes.append(
            "Kc_max used default wind and/or humidity (FAO-56 eq 72 reference "
            "conditions), so peak soil evaporation is approximate."
        )

    kcb_series = kcb_curve(crop, n) if not crop.is_fallow else np.zeros(n)
    zr_series = root_depth_curve(crop, n, bare_profile_depth_m)
    kcb_full = max(crop.kc.mid, kc_min + 1e-6)
    height = crop.root.height_m

    taw_per_m = texture.taw_mm_per_m
    tew = texture.tew_mm(surface_layer_depth_m)
    rew = min(texture.rew_mm, tew)

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

        # 3. irrigation, decided on the depletion at the start of the day
        irr_net = irr_gross = 0.0
        if irrigation.is_irrigated and dr >= irrigation.trigger_depletion_frac * raw:
            target = irrigation.refill_to_depletion_frac * taw
            want = max(dr - target, 0.0)
            irr_net = min(want, irrigation.max_application_mm)
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
