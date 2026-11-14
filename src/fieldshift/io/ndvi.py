"""MODIS NDVI via NASA AppEEARS: the satellite's opinion of when a field is green.

This closes the one validation gap nothing else could reach.

FieldShift reports a `bare_ground` fraction - how much of the year the soil sits
naked - and it is one of the six measures a rotation is judged on. Until now it
was the only measure we could not check anywhere, in either tier. The US
validation leans on county yield statistics, and a yield statistic says nothing
about when the ground was covered.

A satellite does. MODIS reports a vegetation index every 16 days at 250 m, back
to 2000, everywhere. Bare soil sits near 0.1-0.2; a closed canopy is above 0.6.
So the question becomes answerable: **when our model says this field is bare, is
it actually bare?**

### What this can and cannot validate

It CANNOT validate a rotation we invented. The satellite sees whatever the farmer
actually planted, and our alternative rotations were never grown. Comparing our
simulated sorghum against a pixel that grew wheat would be meaningless.

It CAN validate the EXISTING rotation - the one the farmer already grows, which is
the one in the pixel. That is enough to test the three things that matter:

  1. **Timing.** Does our crop green up and senesce when the real one does? This
     tests the thermal model and the sowing dates together.
  2. **Bare periods.** In a wheat-fallow system the fallow year should read as
     bare for most of a year. Does it?
  3. **Magnitude.** Our modelled bare fraction against the observed fraction of
     time the pixel sits below a bare-soil threshold.

### Three traps in this data, all handled explicitly

**Scaling.** MODIS NDVI ships as a 16-bit integer scaled by 10,000. An unscaled
value of 6,231 is not an NDVI, and NDVI is bounded on [-1, 1] - so anything
outside that after scaling is a parsing error, not an unusual field.

**Fill values.** -3000 is the documented fill. Left in, it drags every mean down
and looks like a bare field rather than a missing observation.

**Clouds.** This is the real one. MODIS is optical, so a cloudy composite is a bad
observation, not a brown field - and a cloudy monsoon in Punjab would read as bare
ground in exactly the season the crop is densest. The product ships a pixel
reliability band for precisely this, and we keep only reliable pixels rather than
averaging good and bad together.
"""

from __future__ import annotations

import csv
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

APPEEARS = "https://appeears.earthdatacloud.nasa.gov/api"

# MODIS MOD13Q1 conventions, from the product documentation.
NDVI_SCALE = 10_000.0
NDVI_FILL = -3000
NDVI_VALID = (-1.0, 1.0)

# Pixel reliability: 0 = good, 1 = marginal, 2 = snow/ice, 3 = cloudy, -1 = fill.
# Marginal is kept because discarding it thins the record badly in monsoon
# seasons, which is exactly where we most need observations - but snow and cloud
# are discarded, because a white or obscured pixel carries no information about
# whether the ground below it is covered.
RELIABILITY_KEEP = (0, 1)

# Below this, a pixel is reporting bare or near-bare ground rather than a crop.
# It is a threshold, not a measurement, so it is a named constant and the
# validation reports its sensitivity rather than hiding behind one value.
BARE_NDVI = 0.25


class NdviError(ValueError):
    pass


@dataclass(frozen=True)
class NdviSeries:
    """A clean NDVI time series for one point."""

    site_id: str
    dates: np.ndarray          # datetime64[D]
    ndvi: np.ndarray           # float, -1..1
    kept: int
    rejected: dict[str, int]

    def __len__(self) -> int:
        return len(self.dates)

    @property
    def years(self) -> np.ndarray:
        return self.dates.astype("datetime64[Y]").astype(int) + 1970

    def bare_fraction(self, threshold: float = BARE_NDVI) -> float:
        """Share of observations reading as bare ground.

        A fraction of OBSERVATIONS, not of days. MODIS composites are evenly
        spaced at 16 days, so the two are close - but cloud screening removes
        observations unevenly, and in a monsoon climate it removes them from
        exactly the greenest part of the year. The difference is reported rather
        than assumed away.
        """
        if not len(self.ndvi):
            raise NdviError(f"{self.site_id}: no usable observations")
        return float((self.ndvi < threshold).mean())

    def seasonal_profile(self) -> tuple[np.ndarray, np.ndarray]:
        """Mean NDVI by day of year, which is the shape a crop calendar predicts."""
        doy = (self.dates - self.dates.astype("datetime64[Y]")).astype(int) + 1
        order = np.argsort(doy)
        return doy[order], self.ndvi[order]


def parse_appeears_csv(path: str | Path, *, site_ids=None) -> dict[str, NdviSeries]:
    """Read an AppEEARS point-sample CSV into clean per-site series."""
    path = Path(path)
    if not path.exists():
        raise NdviError(f"no NDVI file at {path}")

    rows = list(csv.DictReader(path.open()))
    if not rows:
        raise NdviError(f"{path} is empty")

    header = rows[0].keys()
    id_col = next((c for c in ("ID", "Category", "id") if c in header), None)
    date_col = next((c for c in ("Date", "Latitude") if c in header), None)
    ndvi_col = next((c for c in header if c.endswith("_250m_16_days_NDVI")), None)
    rel_col = next(
        (c for c in header if c.endswith("_250m_16_days_pixel_reliability")), None
    )
    if not (id_col and ndvi_col):
        raise NdviError(
            f"unexpected AppEEARS columns; found {sorted(header)[:12]}"
        )
    if date_col != "Date":
        date_col = "Date" if "Date" in header else None
    if date_col is None:
        raise NdviError("AppEEARS CSV has no Date column")

    by_site: dict[str, dict[str, list]] = {}
    tallies: dict[str, dict[str, int]] = {}
    for row in rows:
        sid = row[id_col]
        if site_ids and sid not in site_ids:
            continue
        tally = tallies.setdefault(
            sid, {"fill": 0, "unreliable": 0, "out of range": 0, "unparsable": 0}
        )
        store = by_site.setdefault(sid, {"dates": [], "ndvi": []})

        raw = row.get(ndvi_col, "")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            tally["unparsable"] += 1
            continue

        # The fill value must go before scaling, or -3000/10000 = -0.3 looks like
        # a plausible (if odd) NDVI rather than a missing observation.
        if int(round(value)) == NDVI_FILL:
            tally["fill"] += 1
            continue

        if rel_col is not None:
            try:
                reliability = int(float(row.get(rel_col, "")))
            except (TypeError, ValueError):
                reliability = -1
            if reliability not in RELIABILITY_KEEP:
                tally["unreliable"] += 1
                continue

        # AppEEARS may deliver NDVI already scaled or as the raw integer,
        # depending on the request. Detect rather than assume: a real NDVI can
        # never exceed 1, so anything larger is still in raw counts.
        ndvi = value / NDVI_SCALE if abs(value) > 1.5 else value
        if not NDVI_VALID[0] <= ndvi <= NDVI_VALID[1]:
            tally["out of range"] += 1
            continue

        store["dates"].append(np.datetime64(row[date_col][:10]))
        store["ndvi"].append(ndvi)

    out: dict[str, NdviSeries] = {}
    for sid, store in by_site.items():
        if not store["dates"]:
            continue
        dates = np.array(store["dates"], dtype="datetime64[D]")
        order = np.argsort(dates)
        out[sid] = NdviSeries(
            site_id=sid,
            dates=dates[order],
            ndvi=np.array(store["ndvi"])[order],
            kept=len(order),
            rejected=tallies.get(sid, {}),
        )
    if not out:
        raise NdviError(f"{path} yielded no usable observations for any site")
    return out


# ------------------------------------------------------------------ fetching


def _token(netrc_host: str = "urs.earthdata.nasa.gov") -> str:
    """Exchange Earthdata credentials for an AppEEARS bearer token.

    Credentials are read from ~/.netrc and handed straight to curl. They are
    never interpolated into a shell string, never logged, and never written
    anywhere by this module.
    """
    import netrc

    try:
        auth = netrc.netrc().authenticators(netrc_host)
    except FileNotFoundError as exc:
        raise NdviError("no ~/.netrc, so AppEEARS cannot authenticate") from exc
    if not auth:
        raise NdviError(f"~/.netrc has no entry for {netrc_host}")
    user, _, password = auth

    out = subprocess.run(
        ["curl", "-sS", "--fail", "--max-time", "90", "-X", "POST",
         f"{APPEEARS}/login", "-u", f"{user}:{password}", "--data-raw", ""],
        capture_output=True, check=False,
    )
    try:
        payload = json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        raise NdviError(f"AppEEARS login returned non-JSON: {out.stderr[:200]}") from exc
    if "token" not in payload:
        raise NdviError(f"AppEEARS login refused: {payload}")
    return payload["token"]


def submit_point_task(
    sites: list[tuple[str, float, float]],
    *,
    start: str = "01-01-2003",
    end: str = "12-31-2024",
    task_name: str = "fieldshift_ndvi",
) -> str:
    """Queue a point-sample task and return its id.

    AppEEARS is asynchronous by design - a multi-year request is minutes to hours
    of someone else's compute - so this returns an id rather than blocking.
    """
    token = _token()
    task = {
        "task_type": "point",
        "task_name": task_name,
        "params": {
            "dates": [{"startDate": start, "endDate": end}],
            "layers": [
                {"product": "MOD13Q1.061", "layer": "_250m_16_days_NDVI"},
                {"product": "MOD13Q1.061",
                 "layer": "_250m_16_days_pixel_reliability"},
            ],
            "coordinates": [
                {"latitude": lat, "longitude": lon, "id": sid, "category": "field"}
                for sid, lat, lon in sites
            ],
        },
    }
    out = subprocess.run(
        ["curl", "-sS", "--fail", "--max-time", "120", "-X", "POST",
         f"{APPEEARS}/task", "-H", f"Authorization: Bearer {token}",
         "-H", "Content-Type: application/json",
         "--data-binary", json.dumps(task)],
        capture_output=True, check=False,
    )
    try:
        payload = json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        raise NdviError(f"AppEEARS task submission failed: {out.stderr[:200]}") from exc
    if "task_id" not in payload:
        raise NdviError(f"AppEEARS refused the task: {payload}")
    return payload["task_id"]


def task_status(task_id: str) -> dict:
    token = _token()
    out = subprocess.run(
        ["curl", "-sS", "--max-time", "90", f"{APPEEARS}/task/{task_id}",
         "-H", f"Authorization: Bearer {token}"],
        capture_output=True, check=False,
    )
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as exc:
        raise NdviError("AppEEARS status returned non-JSON") from exc
