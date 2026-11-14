"""The internal weather contract every physical model depends on.

This is the seam between NASA data and the science. Models import from here and
never touch a granule, a HDF variable name or a product version, so when the
dataset layer changes - a different SMAP product, IMERG Late instead of Final,
POWER instead of a reanalysis - only the adapters move.

Required variables are the ones no plausible source omits: daily maximum and
minimum temperature, and precipitation. Everything else is optional, because
ET0 can be computed with documented substitutions when a variable is missing.
Which substitutions were used is recorded rather than hidden, so an evidence card
can state the actual basis of a number a farmer is being shown.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

import numpy as np

OPTIONAL_VARS = (
    "rh_mean_pct",
    "rh_max_pct",
    "rh_min_pct",
    "wind_2m_ms",
    "solar_mj_m2",
)
REQUIRED_VARS = ("tmax_c", "tmin_c", "precip_mm")


class WeatherError(ValueError):
    """Weather input violates the contract. Fail loudly rather than model nonsense."""


@dataclass(frozen=True)
class Site:
    """Where the field is. Elevation matters: it sets atmospheric pressure."""

    latitude_deg: float
    longitude_deg: float
    elevation_m: float
    name: str = ""

    def __post_init__(self) -> None:
        if not -90.0 <= self.latitude_deg <= 90.0:
            raise WeatherError(f"latitude {self.latitude_deg} out of range")
        if not -180.0 <= self.longitude_deg <= 180.0:
            raise WeatherError(f"longitude {self.longitude_deg} out of range")
        if not -500.0 <= self.elevation_m <= 9000.0:
            raise WeatherError(f"elevation {self.elevation_m} m implausible")

    @property
    def latitude_rad(self) -> float:
        return np.deg2rad(self.latitude_deg)


@dataclass(frozen=True)
class DailyWeather:
    """A gap-free daily series for one site.

    Gap-free is enforced, not assumed: a water balance carries soil moisture from
    one day to the next, so a silently skipped day does not lose one day of data,
    it corrupts every day after it.
    """

    dates: np.ndarray  # datetime64[D], strictly increasing, one day apart
    tmax_c: np.ndarray
    tmin_c: np.ndarray
    precip_mm: np.ndarray
    rh_mean_pct: np.ndarray | None = None
    rh_max_pct: np.ndarray | None = None
    rh_min_pct: np.ndarray | None = None
    wind_2m_ms: np.ndarray | None = None
    solar_mj_m2: np.ndarray | None = None
    provenance: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.validate()

    # ---------- contract enforcement ----------

    def validate(self) -> None:
        n = len(self.dates)
        if n == 0:
            raise WeatherError("empty weather series")
        if self.dates.dtype != np.dtype("datetime64[D]"):
            raise WeatherError(f"dates must be datetime64[D], got {self.dates.dtype}")

        steps = np.diff(self.dates).astype("timedelta64[D]").astype(int)
        if n > 1 and not np.all(steps == 1):
            bad = np.flatnonzero(steps != 1)[:5]
            raise WeatherError(
                "weather series must be gap-free daily; irregular steps after "
                f"{[str(self.dates[i]) for i in bad]}"
            )

        for name in REQUIRED_VARS + OPTIONAL_VARS:
            arr = getattr(self, name)
            if arr is None:
                continue
            if arr.shape != (n,):
                raise WeatherError(f"{name} has shape {arr.shape}, expected ({n},)")
            if not np.isfinite(arr).all():
                count = int((~np.isfinite(arr)).sum())
                raise WeatherError(
                    f"{name} has {count} non-finite value(s). Fill gaps in the adapter "
                    "layer, where the fill method can be recorded - not here."
                )

        if np.any(self.tmax_c < self.tmin_c):
            bad = int(np.flatnonzero(self.tmax_c < self.tmin_c)[0])
            raise WeatherError(f"tmax below tmin on {self.dates[bad]}")
        if np.any(self.precip_mm < 0):
            raise WeatherError("negative precipitation")
        if np.any(self.tmax_c > 60) or np.any(self.tmin_c < -70):
            raise WeatherError("temperature outside the range of terrestrial records")

        for name in ("rh_mean_pct", "rh_max_pct", "rh_min_pct"):
            arr = getattr(self, name)
            if arr is not None and (np.any(arr < 0) or np.any(arr > 100)):
                raise WeatherError(f"{name} outside 0-100")
        if self.rh_max_pct is not None and self.rh_min_pct is not None:
            if np.any(self.rh_max_pct < self.rh_min_pct):
                raise WeatherError("rh_max below rh_min")
        if self.wind_2m_ms is not None and np.any(self.wind_2m_ms < 0):
            raise WeatherError("negative wind speed")
        if self.solar_mj_m2 is not None:
            if np.any(self.solar_mj_m2 < 0) or np.any(self.solar_mj_m2 > 45):
                raise WeatherError("solar radiation outside 0-45 MJ/m2/day")

    # ---------- derived ----------

    def __len__(self) -> int:
        return len(self.dates)

    @property
    def tmean_c(self) -> np.ndarray:
        """FAO-56 uses the mean of max and min, not the mean of hourly values."""
        return (self.tmax_c + self.tmin_c) / 2.0

    @property
    def trange_c(self) -> np.ndarray:
        return self.tmax_c - self.tmin_c

    @property
    def doy(self) -> np.ndarray:
        """Day of year, 1-based."""
        starts = self.dates.astype("datetime64[Y]")
        return (self.dates - starts).astype("timedelta64[D]").astype(int) + 1

    @property
    def year(self) -> np.ndarray:
        return self.dates.astype("datetime64[Y]").astype(int) + 1970

    @property
    def month(self) -> np.ndarray:
        return self.dates.astype("datetime64[M]").astype(int) % 12 + 1

    def available(self) -> set[str]:
        """Which optional variables are actually present."""
        return {v for v in OPTIONAL_VARS if getattr(self, v) is not None}

    def source_of(self, var: str) -> str:
        return self.provenance.get(var, "unrecorded")

    # ---------- slicing ----------

    def between(self, start: str | np.datetime64, end: str | np.datetime64) -> DailyWeather:
        """Inclusive date slice, preserving provenance."""
        s = np.datetime64(start, "D")
        e = np.datetime64(end, "D")
        if e < s:
            raise WeatherError("end before start")
        mask = (self.dates >= s) & (self.dates <= e)
        if not mask.any():
            raise WeatherError(f"no data between {s} and {e}")
        return self._subset(mask)

    def water_year(self, year: int, start_month: int = 1) -> DailyWeather:
        """One 12-month block, optionally starting at a month other than January.

        Agricultural years rarely align with calendar years - a monsoon season can
        straddle December - so the start month is a parameter rather than fixed.
        """
        if not 1 <= start_month <= 12:
            raise WeatherError("start_month must be 1-12")
        start = np.datetime64(f"{year:04d}-{start_month:02d}", "M")
        end = start + np.timedelta64(12, "M")
        months = self.dates.astype("datetime64[M]")
        mask = (months >= start) & (months < end)
        if not mask.any():
            raise WeatherError(f"no data in the 12 months from {start}")
        return self._subset(mask)

    def _subset(self, mask: np.ndarray) -> DailyWeather:
        arrays = {
            name: (None if getattr(self, name) is None else getattr(self, name)[mask])
            for name in REQUIRED_VARS + OPTIONAL_VARS
        }
        return replace(self, dates=self.dates[mask], **arrays)

    # ---------- aggregation ----------

    def monthly(self) -> MonthlyWeather:
        """Monthly aggregates, which is the timestep RothC runs on.

        Partial months at the ends of the series are reported with their day count
        so a caller can drop them; silently averaging a 9-day month would bias a
        carbon run.
        """
        keys = self.dates.astype("datetime64[M]")
        uniq, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)

        def mean_by(arr: np.ndarray) -> np.ndarray:
            total = np.zeros(len(uniq))
            np.add.at(total, inverse, arr)
            return total / counts

        def sum_by(arr: np.ndarray) -> np.ndarray:
            total = np.zeros(len(uniq))
            np.add.at(total, inverse, arr)
            return total

        return MonthlyWeather(
            months=uniq,
            tmean_c=mean_by(self.tmean_c),
            precip_mm=sum_by(self.precip_mm),
            days=counts,
        )

    # ---------- construction ----------

    @classmethod
    def from_records(
        cls, records: Sequence[Mapping[str, object]], provenance: Mapping[str, str] | None = None
    ) -> DailyWeather:
        """Build from a list of per-day dicts - the shape an adapter naturally produces.

        A variable is treated as present only if every record supplies it. Partial
        coverage is rejected rather than silently gap-filled, because a half-present
        wind series would quietly change the ET0 method mid-run.
        """
        if not records:
            raise WeatherError("no records")
        rows = sorted(records, key=lambda r: str(r["date"]))
        dates = np.array([np.datetime64(str(r["date"]), "D") for r in rows])

        def column(name: str, required: bool) -> np.ndarray | None:
            present = [name in r and r[name] is not None for r in rows]
            if all(present):
                return np.array([float(r[name]) for r in rows], dtype=float)
            if required:
                raise WeatherError(f"{name} is required but missing from some records")
            if any(present):
                raise WeatherError(
                    f"{name} is present for only {sum(present)}/{len(rows)} days; "
                    "fill or drop it in the adapter so the ET0 method stays consistent"
                )
            return None

        return cls(
            dates=dates,
            tmax_c=column("tmax_c", True),
            tmin_c=column("tmin_c", True),
            precip_mm=column("precip_mm", True),
            **{v: column(v, False) for v in OPTIONAL_VARS},
            provenance=dict(provenance or {}),
        )


@dataclass(frozen=True)
class MonthlyWeather:
    """Monthly aggregates. RothC's native timestep."""

    months: np.ndarray  # datetime64[M]
    tmean_c: np.ndarray
    precip_mm: np.ndarray
    days: np.ndarray

    def __len__(self) -> int:
        return len(self.months)

    @property
    def month_number(self) -> np.ndarray:
        return self.months.astype(int) % 12 + 1

    @property
    def year(self) -> np.ndarray:
        return self.months.astype("datetime64[Y]").astype(int) + 1970

    def complete(self) -> MonthlyWeather:
        """Drop partial months at the ends of the series."""
        expected = (
            (self.months + np.timedelta64(1, "M")).astype("datetime64[D]")
            - self.months.astype("datetime64[D]")
        ).astype(int)
        mask = self.days == expected
        return MonthlyWeather(
            months=self.months[mask],
            tmean_c=self.tmean_c[mask],
            precip_mm=self.precip_mm[mask],
            days=self.days[mask],
        )
