"""USDA NASS QuickStats adapter: observed county yields, our validation ground truth.

This is the dataset that lets the tool be checked rather than merely asserted. For
the pilot county it carries county yields back to 1984, split by production
practice - irrigated against non-irrigated, and for wheat even split by whether the
crop followed summer fallow or continuous cropping. That last split is a directly
observed rotation effect, which is the strongest validation target available to a
rotation model: not "does our stress index track weather" but "does our model
reproduce a yield difference that rotation actually produced in this county".

One methodological point is built in rather than left to the caller. County yields
trend strongly upward over four decades from genetics and agronomy, and that trend
has nothing to do with weather. Correlating a weather-driven stress index against
raw yields would mostly measure the trend. `detrend` removes it and returns the
residual, which is the part weather can explain.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

QUICKSTATS_URL = "https://quickstats.nass.usda.gov/api/api_GET/"

# Production-practice strings as NASS spells them.
ALL_PRACTICES = "ALL PRODUCTION PRACTICES"
IRRIGATED = "IRRIGATED"
NON_IRRIGATED = "NON-IRRIGATED"
NON_IRRIGATED_AFTER_FALLOW = "NON-IRRIGATED, FOLLOWING SUMMER FALLOW"
NON_IRRIGATED_CONTINUOUS = "NON-IRRIGATED, CONTINUOUS CROP"


class QuickStatsError(ValueError):
    pass


@dataclass(frozen=True)
class YieldSeries:
    """Observed county yield by year, with its own provenance."""

    commodity: str
    practice: str
    unit: str
    county: str
    state: str
    years: np.ndarray
    values: np.ndarray

    def __len__(self) -> int:
        return len(self.years)

    @property
    def label(self) -> str:
        return f"{self.commodity} ({self.practice})"

    def detrend(self, degree: int = 1) -> np.ndarray:
        """Yield residual after removing the technology trend.

        Four decades of county yield carry a large genetic and agronomic trend that
        is unrelated to weather. The residual is the part a weather-driven stress
        index could possibly explain; correlating against raw yield would mostly
        measure the trend and would flatter any model.
        """
        if len(self) <= degree + 1:
            raise QuickStatsError(
                f"{self.label}: {len(self)} observations is too few to detrend"
            )
        fit = np.polyfit(self.years, self.values, degree)
        return self.values - np.polyval(fit, self.years)

    def trend_per_year(self) -> float:
        if len(self) < 3:
            raise QuickStatsError(f"{self.label}: too few observations for a trend")
        return float(np.polyfit(self.years, self.values, 1)[0])

    def common_years(self, other: "YieldSeries") -> np.ndarray:
        return np.intersect1d(self.years, other.years)


def fetch_yields(
    api_key: str,
    *,
    commodity: str,
    state_alpha: str,
    county_name: str,
    practice: str = ALL_PRACTICES,
    unit: str | None = None,
    start_year: int = 1984,
    cache_dir: str | Path = "data/raw",
    timeout_s: float = 120.0,
) -> YieldSeries:
    """Fetch one county yield series, cached on disk.

    Caching is not optional in practice: QuickStats is rate-limited and a hackathon
    cannot afford to re-request the same forty years repeatedly.
    """
    cache = Path(cache_dir) / (
        f"quickstats_{state_alpha}_{county_name}_{commodity}_"
        f"{practice.replace(' ', '_').replace(',', '')}.json".lower()
    )
    if cache.exists():
        rows = json.loads(cache.read_text())
    else:
        params = {
            "key": api_key,
            "source_desc": "SURVEY",
            "agg_level_desc": "COUNTY",
            "statisticcat_desc": "YIELD",
            "commodity_desc": commodity,
            "state_alpha": state_alpha,
            "county_name": county_name,
            "prodn_practice_desc": practice,
            "year__GE": str(start_year),
            "format": "JSON",
        }
        url = QUICKSTATS_URL + "?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as r:
                rows = json.loads(r.read())["data"]
        except urllib.error.HTTPError as e:
            raise QuickStatsError(
                f"QuickStats returned HTTP {e.code} for {commodity}/{practice}. "
                "A 401 means the key is wrong; a 400 usually means no records match."
            ) from None
        except (urllib.error.URLError, KeyError) as e:
            raise QuickStatsError(f"QuickStats request failed: {e}") from None
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rows))

    return parse_yield_rows(rows, commodity, practice, unit)


def parse_yield_rows(
    rows: list[dict], commodity: str, practice: str, unit: str | None = None
) -> YieldSeries:
    """Turn QuickStats records into a clean annual series. Pure, so it is testable."""
    if not rows:
        raise QuickStatsError(f"no records for {commodity}/{practice}")

    units = {r.get("unit_desc") for r in rows}
    if unit is None:
        # Per-acre yields are the comparable form; "NET PLANTED ACRE" and tonnage
        # variants coexist in the same response and must not be mixed.
        preferred = [u for u in units if u and u.endswith("/ ACRE") and "NET" not in u]
        if not preferred:
            raise QuickStatsError(f"no per-acre unit among {sorted(units)}")
        unit = sorted(preferred)[0]

    by_year: dict[int, float] = {}
    for r in rows:
        if r.get("unit_desc") != unit:
            continue
        raw = str(r.get("Value", "")).replace(",", "").strip()
        if not raw or raw.startswith("("):  # NASS suppresses small samples as "(D)"
            continue
        try:
            by_year[int(r["year"])] = float(raw)
        except (TypeError, ValueError):
            continue
    if not by_year:
        raise QuickStatsError(f"no usable values for {commodity}/{practice} in {unit}")

    years = np.array(sorted(by_year))
    return YieldSeries(
        commodity=commodity,
        practice=practice,
        unit=unit,
        county=str(rows[0].get("county_name", "")),
        state=str(rows[0].get("state_alpha", "")),
        years=years,
        values=np.array([by_year[y] for y in years], dtype=float),
    )


def skill(predicted: np.ndarray, observed_residual: np.ndarray) -> dict[str, float]:
    """Correlation between a model index and a detrended yield anomaly.

    Reported with n and the two-sided p-value, because a correlation over twenty
    county-years can look impressive and mean very little.
    """
    if len(predicted) != len(observed_residual):
        raise QuickStatsError("series lengths differ")
    n = len(predicted)
    if n < 5:
        raise QuickStatsError(f"only {n} paired years; not enough to report skill")
    r = float(np.corrcoef(predicted, observed_residual)[0, 1])
    # Two-sided t-test on the correlation coefficient.
    from scipy import stats

    t = r * np.sqrt((n - 2) / max(1e-12, 1 - r**2))
    p = float(2 * stats.t.sf(abs(t), df=n - 2))
    return {"r": r, "r2": r**2, "n": float(n), "p_value": p}
