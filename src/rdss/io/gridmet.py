"""gridMET adapter: 4 km CONUS daily meteorology and reference ET.

gridMET matters here for one reason: it publishes grass reference ET as a product,
which makes it a direct, independent check on our FAO-56 Penman-Monteith ET0
rather than an input to it.

It also carries a trap worth encoding. The THREDDS NetCDF Subset Service CSV
export returns the variable's PACKED integer values and does not apply the
`scale_factor` recorded in the file's metadata. For reference ET that factor is
0.1, so an unguarded read yields 42 mm/day - physically impossible, but large
enough to look like a plausible "our model is biased" conclusion if nobody checks
the magnitude. It briefly did exactly that here.

So this adapter applies the scale factor explicitly and then refuses values outside
physical bounds. A plausibility guard on a well-known quantity costs nothing and
catches the whole class of silent unit errors.
"""

from __future__ import annotations

import csv
import io
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

THREDDS_NCSS = "https://thredds.northwestknowledge.net/thredds/ncss"

VARIABLES = {
    "eto": ("agg_met_pet_1979_CurrentYear_CONUS",
            "daily_mean_reference_evapotranspiration_grass"),
    "etr": ("agg_met_etr_1979_CurrentYear_CONUS",
            "daily_mean_reference_evapotranspiration_alfalfa"),
    "precip": ("agg_met_pr_1979_CurrentYear_CONUS", "precipitation_amount"),
    "tmax": ("agg_met_tmmx_1979_CurrentYear_CONUS", "air_temperature"),
    "tmin": ("agg_met_tmmn_1979_CurrentYear_CONUS", "air_temperature"),
}

# Physical bounds for a daily value, used to catch unapplied scale factors.
PLAUSIBLE_RANGE = {
    "eto": (0.0, 20.0),      # mm/day
    "etr": (0.0, 25.0),      # mm/day, alfalfa reference runs higher
    "precip": (0.0, 500.0),  # mm/day
    "tmax": (180.0, 340.0),  # K as stored
    "tmin": (180.0, 340.0),
}


class GridmetError(ValueError):
    pass


@dataclass(frozen=True)
class GridmetSeries:
    dates: np.ndarray  # datetime64[D]
    values: np.ndarray
    variable: str
    units: str
    scale_factor: float
    source: str

    def __len__(self) -> int:
        return len(self.dates)

    def annual_totals(self) -> dict[int, float]:
        years = self.dates.astype("datetime64[Y]").astype(int) + 1970
        return {int(y): float(self.values[years == y].sum()) for y in np.unique(years)}


def dataset_scale_factor(variable: str, timeout_s: float = 30.0) -> tuple[float, float]:
    """Read scale_factor and add_offset from the dataset metadata.

    Fetched rather than hard-coded: if gridMET ever repacks a variable, a stale
    constant would silently rescale every number downstream.
    """
    dataset, var = VARIABLES[variable]
    url = f"{THREDDS_NCSS}/{dataset}.nc/dataset.xml"
    with urllib.request.urlopen(url, timeout=timeout_s) as r:
        xml = r.read().decode("utf-8", "replace")
    i = xml.find(var)
    if i < 0:
        raise GridmetError(f"variable {var!r} not found in {url}")
    segment = xml[max(0, i - 400): i + 1600]

    def attr(name: str, default: float) -> float:
        m = re.search(rf'name="{name}"[^>]*value="([^"]*)"', segment)
        return float(m.group(1)) if m else default

    return attr("scale_factor", 1.0), attr("add_offset", 0.0)


def parse_gridmet_csv(
    text: str,
    variable: str,
    scale_factor: float,
    add_offset: float = 0.0,
    *,
    enforce_plausibility: bool = True,
) -> GridmetSeries:
    """Parse an NCSS CSV export, applying the packing transform.

    Raises if the result is physically implausible, which is what an unapplied
    scale factor looks like.
    """
    if variable not in VARIABLES:
        raise GridmetError(f"unknown variable {variable!r}; have {sorted(VARIABLES)}")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise GridmetError("empty CSV response")
    try:
        tcol = next(c for c in reader.fieldnames if "time" in c.lower())
        vcol = next(
            c for c in reader.fieldnames
            if VARIABLES[variable][1].split("[")[0] in c or "amount" in c or "temperature" in c
        )
    except StopIteration:
        raise GridmetError(f"no time/value column in {reader.fieldnames}") from None

    units_match = re.search(r'unit="([^"]*)"', vcol)
    units = units_match.group(1) if units_match else ""

    dates, raw = [], []
    for row in reader:
        try:
            value = float(row[vcol])
        except (TypeError, ValueError):
            continue
        dates.append(np.datetime64(row[tcol][:10], "D"))
        raw.append(value)
    if not dates:
        raise GridmetError("CSV contained no parseable rows")

    order = np.argsort(np.array(dates))
    d = np.array(dates)[order]
    values = np.array(raw, dtype=float)[order] * scale_factor + add_offset

    if enforce_plausibility:
        lo, hi = PLAUSIBLE_RANGE[variable]
        out_of_range = (values < lo) | (values > hi)
        if out_of_range.any():
            worst = float(np.nanmax(np.abs(values[out_of_range])))
            raise GridmetError(
                f"{variable} values outside the plausible range {lo}-{hi} after "
                f"applying scale_factor={scale_factor} (worst |value| {worst:g}). "
                "The most likely cause is a wrong or unapplied packing transform - "
                "NCSS CSV exports return packed integers."
            )

    return GridmetSeries(
        dates=d, values=values, variable=variable, units=units,
        scale_factor=scale_factor, source=f"gridMET {VARIABLES[variable][0]}",
    )


def fetch_gridmet_point(
    variable: str,
    latitude: float,
    longitude: float,
    start_year: int,
    end_year: int,
    *,
    cache_dir: str | Path = "data/raw",
    chunk_years: int = 10,
    timeout_s: float = 300.0,
) -> GridmetSeries:
    """Fetch a point time series, chunked and cached.

    Chunking is required, not an optimisation: a single multi-decade NCSS request
    is silently truncated rather than refused. A 41-year request came back with 30
    years and no error, which would have quietly shortened the record.
    """
    dataset, var = VARIABLES[variable]
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    scale, offset = dataset_scale_factor(variable)

    chunks: list[GridmetSeries] = []
    for a in range(start_year, end_year + 1, chunk_years):
        b = min(a + chunk_years - 1, end_year)
        path = cache / f"gridmet_{variable}_{latitude:.3f}_{longitude:.3f}_{a}_{b}.csv"
        if not path.exists():
            url = (
                f"{THREDDS_NCSS}/{dataset}.nc?var={var}"
                f"&latitude={latitude}&longitude={longitude}"
                f"&time_start={a}-01-01T00:00:00Z&time_end={b}-12-31T00:00:00Z&accept=csv"
            )
            with urllib.request.urlopen(url, timeout=timeout_s) as r:
                path.write_text(r.read().decode("utf-8", "replace"), encoding="utf-8")
        chunks.append(parse_gridmet_csv(path.read_text(encoding="utf-8"), variable, scale, offset))

    dates = np.concatenate([c.dates for c in chunks])
    values = np.concatenate([c.values for c in chunks])
    keep = np.unique(dates, return_index=True)[1]
    return GridmetSeries(
        dates=dates[keep], values=values[keep], variable=variable,
        units=chunks[0].units, scale_factor=scale, source=chunks[0].source,
    )
