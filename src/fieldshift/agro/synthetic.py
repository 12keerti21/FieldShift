"""A seeded stochastic weather generator, for exercising models before real data.

This exists so the physical models can be written, run and validated over
multi-year sequences now, rather than being first executed on real data at the
point when any failure is ambiguous between a model bug and a data problem.

The structure is a Richardson-type generator: a deterministic annual temperature
cycle with noise, and precipitation as a two-state Markov chain whose wet-day
probability varies by month, with gamma-distributed amounts. Two couplings are
deliberate rather than cosmetic - wet days have a compressed diurnal temperature
range and higher humidity - because ET0 estimates radiation from diurnal range,
so a generator without that coupling would produce physically impossible weather
(heavy rain under cloudless skies) and would flatter the water balance.

Output is NEVER a substitute for observations. Every series is tagged
"synthetic" in its provenance so no result built on it can be mistaken for one
built on NASA data.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .climate import DailyWeather, Site
from .et0 import clear_sky_radiation, extraterrestrial_radiation

DAYS_IN_YEAR = 365.25


@dataclass(frozen=True)
class ClimateArchetype:
    """A coarse climate description sufficient to generate plausible daily weather."""

    id: str
    name: str
    tmax_mean_c: float
    tmax_amplitude_c: float
    tmax_peak_doy: int
    tmin_offset_c: float  # mean depression of tmin below tmax on a dry day
    tmin_amplitude_c: float
    temp_noise_sd_c: float
    wet_day_prob: tuple[float, ...]  # 12 monthly values
    rain_per_wet_day_mm: tuple[float, ...]  # 12 monthly means
    rain_gamma_shape: float
    wet_persistence: float  # 0 = independent days, ->1 = long spells
    rh_dry_pct: float
    rh_wet_pct: float
    wind_mean_ms: float
    wind_sd_ms: float
    wet_day_range_compression: float  # fraction by which rain narrows tmax-tmin
    notes: str

    def __post_init__(self) -> None:
        if len(self.wet_day_prob) != 12 or len(self.rain_per_wet_day_mm) != 12:
            raise ValueError("monthly series must have 12 entries")
        if not all(0.0 <= p <= 1.0 for p in self.wet_day_prob):
            raise ValueError("wet-day probabilities must be in [0, 1]")
        if not 0.0 <= self.wet_persistence < 1.0:
            raise ValueError("wet_persistence must be in [0, 1)")


# Monthly arrays run January to December.
ARCHETYPES: dict[str, ClimateArchetype] = {
    "semi_arid_monsoon": ClimateArchetype(
        id="semi_arid_monsoon",
        name="Semi-arid monsoon (Deccan-like)",
        tmax_mean_c=31.0,
        tmax_amplitude_c=4.5,
        tmax_peak_doy=110,
        tmin_offset_c=12.0,
        tmin_amplitude_c=5.0,
        temp_noise_sd_c=1.6,
        wet_day_prob=(0.02, 0.02, 0.03, 0.05, 0.10, 0.30, 0.40, 0.38, 0.28, 0.15, 0.05, 0.02),
        rain_per_wet_day_mm=(5, 5, 6, 8, 10, 12, 12, 12, 12, 11, 7, 5),
        rain_gamma_shape=0.8,
        wet_persistence=0.35,
        rh_dry_pct=50.0,
        rh_wet_pct=84.0,
        wind_mean_ms=1.8,
        wind_sd_ms=0.7,
        wet_day_range_compression=0.45,
        notes=(
            "Long dry season with a concentrated monsoon. The dry-season bare window "
            "and the onset date are the decision-relevant features."
        ),
    ),
    "indo_gangetic": ClimateArchetype(
        id="indo_gangetic",
        name="Humid subtropical monsoon (Indo-Gangetic-like)",
        tmax_mean_c=31.5,
        tmax_amplitude_c=8.5,
        tmax_peak_doy=150,
        tmin_offset_c=11.0,
        tmin_amplitude_c=9.0,
        temp_noise_sd_c=2.0,
        wet_day_prob=(0.05, 0.05, 0.04, 0.03, 0.07, 0.25, 0.52, 0.50, 0.30, 0.08, 0.02, 0.03),
        rain_per_wet_day_mm=(7, 8, 7, 6, 10, 16, 20, 19, 16, 10, 5, 6),
        rain_gamma_shape=0.75,
        wet_persistence=0.40,
        rh_dry_pct=55.0,
        rh_wet_pct=88.0,
        wind_mean_ms=1.7,
        wind_sd_ms=0.7,
        wet_day_range_compression=0.45,
        notes=(
            "Hot pre-monsoon, intense monsoon, cool dry winter - the classic "
            "rice-wheat window. Terminal heat in the rabi season is the emerging risk."
        ),
    ),
    "temperate_continental": ClimateArchetype(
        id="temperate_continental",
        name="Temperate continental",
        tmax_mean_c=15.0,
        tmax_amplitude_c=13.0,
        tmax_peak_doy=200,
        tmin_offset_c=9.0,
        tmin_amplitude_c=10.0,
        temp_noise_sd_c=3.2,
        wet_day_prob=(0.28, 0.26, 0.28, 0.27, 0.28, 0.26, 0.23, 0.22, 0.22, 0.25, 0.28, 0.30),
        rain_per_wet_day_mm=(5, 5, 6, 7, 9, 11, 11, 11, 9, 7, 6, 5),
        rain_gamma_shape=0.9,
        wet_persistence=0.28,
        rh_dry_pct=70.0,
        rh_wet_pct=90.0,
        wind_mean_ms=2.5,
        wind_sd_ms=1.0,
        wet_day_range_compression=0.40,
        notes="Rain spread through the year; frost and season length are the constraints.",
    ),
    "mediterranean": ClimateArchetype(
        id="mediterranean",
        name="Mediterranean (winter rain, dry summer)",
        tmax_mean_c=23.0,
        tmax_amplitude_c=9.0,
        tmax_peak_doy=205,
        tmin_offset_c=10.0,
        tmin_amplitude_c=7.0,
        temp_noise_sd_c=2.4,
        wet_day_prob=(0.30, 0.27, 0.24, 0.17, 0.09, 0.03, 0.01, 0.01, 0.06, 0.16, 0.26, 0.31),
        rain_per_wet_day_mm=(10, 9, 8, 7, 6, 4, 3, 4, 6, 8, 10, 11),
        rain_gamma_shape=0.85,
        wet_persistence=0.32,
        rh_dry_pct=52.0,
        rh_wet_pct=84.0,
        wind_mean_ms=2.8,
        wind_sd_ms=1.0,
        wet_day_range_compression=0.40,
        notes=(
            "Rain and heat are out of phase, so the summer bare window is both the "
            "hottest and the driest part of the year."
        ),
    ),
    "arid": ClimateArchetype(
        id="arid",
        name="Arid",
        tmax_mean_c=32.0,
        tmax_amplitude_c=9.0,
        tmax_peak_doy=190,
        tmin_offset_c=14.0,
        tmin_amplitude_c=8.0,
        temp_noise_sd_c=2.2,
        wet_day_prob=(0.04, 0.04, 0.04, 0.03, 0.02, 0.02, 0.09, 0.11, 0.06, 0.03, 0.03, 0.04),
        rain_per_wet_day_mm=(7, 7, 8, 7, 6, 6, 14, 17, 11, 7, 6, 7),
        rain_gamma_shape=0.65,
        wet_persistence=0.20,
        rh_dry_pct=44.0,
        rh_wet_pct=76.0,
        wind_mean_ms=2.4,
        wind_sd_ms=1.0,
        wet_day_range_compression=0.35,
        notes=(
            "Rainfall is dominated by a few intense events, so totals mislead and the "
            "gamma shape parameter below 1 matters: most rain arrives in few days. "
            "Generated annual ET0 lands near 2100 mm, in line with reported reference "
            "ET0 for hot interior deserts (Jodhpur ~1950, Jaisalmer ~2100)."
        ),
    ),
}


def archetype(name: str) -> ClimateArchetype:
    try:
        return ARCHETYPES[name]
    except KeyError:
        raise KeyError(
            f"unknown climate archetype {name!r}; available: {sorted(ARCHETYPES)}"
        ) from None


def synthetic_weather(
    site: Site,
    climate: str | ClimateArchetype,
    start: str = "2000-01-01",
    years: int = 1,
    seed: int = 0,
    *,
    include_humidity: bool = True,
    include_wind: bool = True,
    include_solar: bool = False,
    warming_trend_c_per_decade: float = 0.0,
    rainfall_trend_frac_per_decade: float = 0.0,
) -> DailyWeather:
    """Generate a seeded daily series.

    ``include_solar`` defaults to False so the ET0 substitution path - estimating
    radiation from diurnal temperature range - is what gets exercised, since that is
    the situation most real datasets will put us in.

    The two trend arguments impose a linear drift on temperature and on rainfall
    amount. They exist so the non-stationarity logic can be tested against a known
    imposed signal: a trend detector that cannot recover a trend put in on purpose
    is not going to find one in the NASA record.
    """
    arch = archetype(climate) if isinstance(climate, str) else climate
    if years < 1:
        raise ValueError("years must be >= 1")

    rng = np.random.default_rng(seed)
    start_date = np.datetime64(start, "D")
    n = int(round(years * DAYS_IN_YEAR))
    dates = np.arange(start_date, start_date + n)

    doy = (dates - dates.astype("datetime64[Y]")).astype("timedelta64[D]").astype(int) + 1
    month = dates.astype("datetime64[M]").astype(int) % 12
    elapsed_decades = np.arange(n) / (DAYS_IN_YEAR * 10.0)

    # --- precipitation: two-state Markov chain with an exact marginal ---
    p = np.asarray(arch.wet_day_prob)[month]
    d = arch.wet_persistence
    p_wet_given_wet = p + d * (1.0 - p)
    p_wet_given_dry = p * (1.0 - d)

    draws = rng.random(n)
    wet = np.zeros(n, dtype=bool)
    wet[0] = draws[0] < p[0]
    for i in range(1, n):
        threshold = p_wet_given_wet[i] if wet[i - 1] else p_wet_given_dry[i]
        wet[i] = draws[i] < threshold

    mean_depth = np.asarray(arch.rain_per_wet_day_mm)[month] * (
        1.0 + rainfall_trend_frac_per_decade * elapsed_decades
    )
    mean_depth = np.maximum(mean_depth, 0.1)
    shape = arch.rain_gamma_shape
    amounts = rng.gamma(shape, mean_depth / shape, size=n)
    precip = np.where(wet, amounts, 0.0)
    precip = np.round(np.maximum(precip, 0.0), 2)
    # A "wet" day that rounds to zero rain is not wet; keep the flag consistent so
    # the humidity and diurnal-range couplings below stay physical.
    wet = precip > 0.0

    # --- temperature ---
    seasonal = np.cos(2.0 * np.pi * (doy - arch.tmax_peak_doy) / DAYS_IN_YEAR)
    warming = warming_trend_c_per_decade * elapsed_decades
    tmax = (
        arch.tmax_mean_c
        + arch.tmax_amplitude_c * seasonal
        + warming
        + rng.normal(0.0, arch.temp_noise_sd_c, n)
    )
    diurnal_range = arch.tmin_offset_c + arch.tmin_amplitude_c * 0.5 * seasonal
    diurnal_range = np.where(
        wet, diurnal_range * (1.0 - arch.wet_day_range_compression), diurnal_range
    )
    diurnal_range = np.maximum(diurnal_range + rng.normal(0.0, 0.8, n), 1.5)
    tmin = tmax - diurnal_range

    optional: dict[str, np.ndarray] = {}
    if include_humidity:
        rh = np.where(wet, arch.rh_wet_pct, arch.rh_dry_pct) + rng.normal(0.0, 5.0, n)
        optional["rh_mean_pct"] = np.clip(rh, 5.0, 100.0)
    if include_wind:
        optional["wind_2m_ms"] = np.maximum(
            rng.normal(arch.wind_mean_ms, arch.wind_sd_ms, n), 0.2
        )
    if include_solar:
        ra = extraterrestrial_radiation(site.latitude_rad, doy)
        rso = clear_sky_radiation(ra, site.elevation_m)
        clear_fraction = np.where(wet, 0.45, 0.92) + rng.normal(0.0, 0.05, n)
        optional["solar_mj_m2"] = np.clip(rso * np.clip(clear_fraction, 0.1, 1.0), 0.0, 45.0)

    return DailyWeather(
        dates=dates,
        tmax_c=np.round(tmax, 2),
        tmin_c=np.round(tmin, 2),
        precip_mm=precip,
        provenance={
            v: f"synthetic:{arch.id}:seed{seed}"
            for v in ("tmax_c", "tmin_c", "precip_mm", *optional)
        },
        **optional,
    )


def annual_rainfall_mm(weather: DailyWeather) -> dict[int, float]:
    """Rainfall total per calendar year, for sanity-checking a generated series."""
    years = weather.year
    return {
        int(y): float(weather.precip_mm[years == y].sum()) for y in np.unique(years)
    }
