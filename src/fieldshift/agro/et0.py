"""Reference evapotranspiration, FAO-56.

ET0 is the atmospheric demand for water from a standard reference surface - a
0.12 m grass canopy with fixed albedo and resistance. It is the quantity that
turns "it was hot and windy" into millimetres a crop had to find, and every other
water number in this tool is derived from it.

Two methods are provided. FAO-56 Penman-Monteith (eq 6) is the standard and is
used whenever possible, including when humidity, wind or radiation have to be
estimated - FAO-56 explicitly recommends that over switching to a simpler
equation. Hargreaves-Samani (eq 52) is kept as an independent cross-check, not as
a fallback: two methods disagreeing is information, and a silent fallback is not.

Every substitution made for a missing variable is recorded on the result, because
a farmer-facing number computed from estimated radiation and assumed wind deserves
to be labelled as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

import numpy as np

from .climate import DailyWeather, Site

# FAO-56 constants
SOLAR_CONSTANT_MJ_M2_MIN = 0.0820  # Gsc
STEFAN_BOLTZMANN_MJ_K4_M2_DAY = 4.903e-9  # sigma
REFERENCE_ALBEDO = 0.23  # grass reference surface
DEFAULT_WIND_MS = 2.0  # FAO-56 recommendation where wind is unmeasured
KRS_INTERIOR = 0.16  # Hargreaves radiation adjustment, interior locations
KRS_COASTAL = 0.19


class ET0Method(str, Enum):
    PENMAN_MONTEITH = "fao56_penman_monteith"
    HARGREAVES = "hargreaves_samani"


@dataclass(frozen=True)
class ET0Result:
    """ET0 in mm/day, plus an honest account of how it was obtained."""

    values_mm: np.ndarray
    method: ET0Method
    substitutions: Mapping[str, str]
    notes: tuple[str, ...] = ()

    def __len__(self) -> int:
        return len(self.values_mm)

    @property
    def confidence(self) -> str:
        """Degrades with each estimated input.

        Radiation matters most: it drives the energy term, which dominates ET0 in
        most climates, so estimating it from diurnal temperature range costs more
        accuracy than assuming a wind speed does.
        """
        subs = set(self.substitutions)
        if not subs:
            return "high"
        if "solar_mj_m2" in subs and len(subs) >= 3:
            return "low"
        if "solar_mj_m2" in subs:
            return "medium"
        return "medium" if len(subs) > 1 else "high"

    def describe(self) -> str:
        if not self.substitutions:
            return f"{self.method.value}, all inputs measured"
        parts = "; ".join(f"{k}: {v}" for k, v in sorted(self.substitutions.items()))
        return f"{self.method.value}, estimated inputs ({parts})"


# ---------------------------------------------------------------- atmosphere


def saturation_vapour_pressure(t_c: np.ndarray | float) -> np.ndarray | float:
    """e0(T), kPa. FAO-56 eq 11 (Tetens form)."""
    return 0.6108 * np.exp(17.27 * t_c / (t_c + 237.3))


def svp_slope(t_c: np.ndarray | float) -> np.ndarray | float:
    """Delta, slope of the saturation vapour pressure curve, kPa/degC. FAO-56 eq 13."""
    return 4098.0 * saturation_vapour_pressure(t_c) / np.square(t_c + 237.3)


def atmospheric_pressure(elevation_m: float) -> float:
    """P, kPa, from a simplified ideal gas relation. FAO-56 eq 7."""
    return 101.3 * ((293.0 - 0.0065 * elevation_m) / 293.0) ** 5.26


def psychrometric_constant(elevation_m: float) -> float:
    """gamma, kPa/degC. FAO-56 eq 8."""
    return 0.665e-3 * atmospheric_pressure(elevation_m)


def wind_to_2m(wind_ms: np.ndarray | float, measurement_height_m: float) -> np.ndarray | float:
    """Adjust wind measured at another height to 2 m. FAO-56 eq 47."""
    if measurement_height_m <= 0:
        raise ValueError("measurement height must be positive")
    if abs(measurement_height_m - 2.0) < 1e-9:
        return wind_ms
    return wind_ms * 4.87 / np.log(67.8 * measurement_height_m - 5.42)


# ---------------------------------------------------------------- radiation


def extraterrestrial_radiation(latitude_rad: float, doy: np.ndarray) -> np.ndarray:
    """Ra, MJ/m2/day, at the top of the atmosphere. FAO-56 eq 21, 23, 24, 25.

    The sunset hour angle argument is clipped to [-1, 1] so polar day and polar
    night return their physical limits instead of a NaN.
    """
    doy = np.asarray(doy, dtype=float)
    dr = 1.0 + 0.033 * np.cos(2.0 * np.pi * doy / 365.0)
    declination = 0.409 * np.sin(2.0 * np.pi * doy / 365.0 - 1.39)
    sunset = np.arccos(
        np.clip(-np.tan(latitude_rad) * np.tan(declination), -1.0, 1.0)
    )
    return (
        (24.0 * 60.0 / np.pi)
        * SOLAR_CONSTANT_MJ_M2_MIN
        * dr
        * (
            sunset * np.sin(latitude_rad) * np.sin(declination)
            + np.cos(latitude_rad) * np.cos(declination) * np.sin(sunset)
        )
    )


def clear_sky_radiation(ra: np.ndarray, elevation_m: float) -> np.ndarray:
    """Rso, MJ/m2/day. FAO-56 eq 37."""
    return (0.75 + 2e-5 * elevation_m) * ra


def solar_from_temperature_range(
    ra: np.ndarray, trange_c: np.ndarray, krs: float = KRS_INTERIOR
) -> np.ndarray:
    """Estimate Rs from the diurnal temperature range. FAO-56 eq 50.

    The physical argument: cloud cover suppresses daytime heating and insulates at
    night, so a small diurnal range implies a cloudy day. It works, but it is the
    weakest link in an ET0 computed without measured radiation - advection, humidity
    and irrigation all compress the range without reducing sunshine.
    """
    return krs * np.sqrt(np.maximum(trange_c, 0.0)) * ra


def net_radiation(
    rs: np.ndarray,
    rso: np.ndarray,
    tmax_c: np.ndarray,
    tmin_c: np.ndarray,
    ea_kpa: np.ndarray,
    albedo: float = REFERENCE_ALBEDO,
) -> np.ndarray:
    """Rn, MJ/m2/day: net shortwave less net longwave. FAO-56 eq 38, 39, 40."""
    rns = (1.0 - albedo) * rs

    tmax_k4 = np.power(tmax_c + 273.16, 4)
    tmin_k4 = np.power(tmin_c + 273.16, 4)
    # Cloudiness factor. Estimated Rs can exceed Rso, and Rso is zero in polar
    # night; both are clipped rather than allowed to produce a nonsense flux.
    ratio = np.where(rso > 0.0, np.clip(rs / np.where(rso > 0.0, rso, 1.0), 0.25, 1.0), 0.75)
    rnl = (
        STEFAN_BOLTZMANN_MJ_K4_M2_DAY
        * ((tmax_k4 + tmin_k4) / 2.0)
        * (0.34 - 0.14 * np.sqrt(np.maximum(ea_kpa, 0.0)))
        * (1.35 * ratio - 0.35)
    )
    return rns - rnl


# ---------------------------------------------------------------- humidity


def _actual_vapour_pressure(
    weather: DailyWeather, arid_dewpoint_offset_c: float
) -> tuple[np.ndarray, str | None]:
    """ea, kPa, by the best route the available humidity data allows.

    Preference order follows FAO-56: RHmax/RHmin (eq 17) is best because it pairs
    each humidity extreme with the temperature it occurred at; RHmean (eq 19) is
    acceptable; dewpoint-from-Tmin (eq 48) is the last resort and assumes the air
    reaches saturation overnight, which fails in arid climates - hence the offset.
    """
    e_tmax = saturation_vapour_pressure(weather.tmax_c)
    e_tmin = saturation_vapour_pressure(weather.tmin_c)

    if weather.rh_max_pct is not None and weather.rh_min_pct is not None:
        ea = (
            e_tmin * weather.rh_max_pct / 100.0 + e_tmax * weather.rh_min_pct / 100.0
        ) / 2.0
        return ea, None

    if weather.rh_mean_pct is not None:
        ea = (weather.rh_mean_pct / 100.0) * (e_tmax + e_tmin) / 2.0
        return ea, None

    ea = saturation_vapour_pressure(weather.tmin_c - arid_dewpoint_offset_c)
    how = (
        "dewpoint assumed equal to daily minimum temperature (FAO-56 eq 48)"
        if arid_dewpoint_offset_c == 0.0
        else f"dewpoint assumed {arid_dewpoint_offset_c:g} degC below daily minimum "
        "temperature (FAO-56 eq 48, arid adjustment)"
    )
    return ea, how


# ---------------------------------------------------------------- ET0


def et0_penman_monteith(
    site: Site,
    weather: DailyWeather,
    *,
    wind_measurement_height_m: float = 2.0,
    default_wind_ms: float = DEFAULT_WIND_MS,
    krs: float = KRS_INTERIOR,
    arid_dewpoint_offset_c: float = 0.0,
) -> ET0Result:
    """FAO-56 Penman-Monteith reference evapotranspiration, mm/day. Eq 6.

    Soil heat flux G is taken as zero, which is standard for a daily timestep: the
    soil gains heat by day and loses it by night, and the residual over 24 hours is
    negligible beside the radiation term.
    """
    substitutions: dict[str, str] = {}
    notes: list[str] = []

    tmean = weather.tmean_c
    delta = svp_slope(tmean)
    gamma = psychrometric_constant(site.elevation_m)

    es = (
        saturation_vapour_pressure(weather.tmax_c)
        + saturation_vapour_pressure(weather.tmin_c)
    ) / 2.0
    ea, ea_substitution = _actual_vapour_pressure(weather, arid_dewpoint_offset_c)
    if ea_substitution:
        substitutions["humidity"] = ea_substitution
    # Estimated ea can exceed es slightly; a negative vapour pressure deficit is
    # unphysical and would produce a negative aerodynamic term.
    vpd = np.maximum(es - ea, 0.0)

    ra = extraterrestrial_radiation(site.latitude_rad, weather.doy)
    rso = clear_sky_radiation(ra, site.elevation_m)
    if weather.solar_mj_m2 is not None:
        rs = weather.solar_mj_m2
    else:
        rs = solar_from_temperature_range(ra, weather.trange_c, krs=krs)
        substitutions["solar_mj_m2"] = (
            f"estimated from diurnal temperature range (FAO-56 eq 50, krs={krs:g})"
        )
    rs = np.minimum(rs, rso)

    if weather.wind_2m_ms is not None:
        u2 = wind_to_2m(weather.wind_2m_ms, wind_measurement_height_m)
    else:
        u2 = np.full(len(weather), float(default_wind_ms))
        substitutions["wind_2m_ms"] = (
            f"assumed {default_wind_ms:g} m/s (FAO-56 default for unmeasured wind)"
        )

    rn = net_radiation(rs, rso, weather.tmax_c, weather.tmin_c, ea)

    numerator = 0.408 * delta * rn + gamma * (900.0 / (tmean + 273.0)) * u2 * vpd
    denominator = delta + gamma * (1.0 + 0.34 * u2)
    et0 = numerator / denominator

    # ET0 can go slightly negative on cold days with strong net longwave loss. The
    # reference surface cannot gain water from the air, so zero is the floor.
    negatives = int((et0 < 0).sum())
    if negatives:
        notes.append(
            f"{negatives} day(s) returned negative ET0 from net longwave loss and "
            "were clipped to zero"
        )
    et0 = np.maximum(et0, 0.0)

    if "solar_mj_m2" in substitutions:
        notes.append(
            "Radiation was estimated, which is the dominant term in most climates - "
            "treat absolute ET0 as indicative and prefer comparisons between "
            "rotations over absolute water-balance figures."
        )

    return ET0Result(
        values_mm=et0,
        method=ET0Method.PENMAN_MONTEITH,
        substitutions=substitutions,
        notes=tuple(notes),
    )


def et0_hargreaves(site: Site, weather: DailyWeather) -> ET0Result:
    """Hargreaves-Samani ET0, mm/day. FAO-56 eq 52.

    Needs only temperature, so it is an independent cross-check on a
    Penman-Monteith run whose radiation and humidity were themselves estimated.
    Used for agreement checking, never as a silent fallback.
    """
    ra = extraterrestrial_radiation(site.latitude_rad, weather.doy)
    ra_mm = 0.408 * ra  # MJ/m2/day to mm/day equivalent evaporation
    et0 = (
        0.0023
        * (weather.tmean_c + 17.8)
        * np.sqrt(np.maximum(weather.trange_c, 0.0))
        * ra_mm
    )
    return ET0Result(
        values_mm=np.maximum(et0, 0.0),
        method=ET0Method.HARGREAVES,
        substitutions={
            "humidity": "not used by this method",
            "wind_2m_ms": "not used by this method",
            "solar_mj_m2": "not used by this method",
        },
        notes=(
            "Temperature-only method. Systematically biased in humid climates and "
            "under advective conditions; intended as a cross-check.",
        ),
    )


def et0(site: Site, weather: DailyWeather, **kwargs) -> ET0Result:
    """Preferred ET0: Penman-Monteith, with substitutions recorded on the result."""
    return et0_penman_monteith(site, weather, **kwargs)


def method_agreement(a: ET0Result, b: ET0Result) -> dict[str, float]:
    """Compare two ET0 estimates. Disagreement is a data-quality signal.

    Reported as a relative mean bias and a mean absolute difference rather than a
    correlation, because both methods share the same seasonal radiation cycle and
    will correlate well even when their magnitudes differ substantially.
    """
    if len(a) != len(b):
        raise ValueError("ET0 series lengths differ")
    mean_a = float(np.mean(a.values_mm))
    mean_b = float(np.mean(b.values_mm))
    denom = mean_b if abs(mean_b) > 1e-9 else np.nan
    return {
        "mean_a_mm": mean_a,
        "mean_b_mm": mean_b,
        "relative_bias": float((mean_a - mean_b) / denom),
        "mean_absolute_difference_mm": float(np.mean(np.abs(a.values_mm - b.values_mm))),
    }
