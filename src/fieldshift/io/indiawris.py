"""India-WRIS adapter: CGWB observation-well water levels, no credentials.

This is the dataset that makes the Punjab tier checkable at all, and finding it
changed what "validating Punjab" even means.

The US validation correlates modelled water stress against detrended county yield.
That method does not transfer here, and not for want of data. Punjab rice is grown
under continuous submergence and wheat is irrigated on demand, so the model says
every year is unstressed - measured over 39 seasons at Sangrur, the relative
transpiration of ponded rice has a standard deviation of **exactly zero** and
irrigated wheat 0.0008. There is nothing to correlate. Obtaining district yields
would not have fixed that; the signal is absent by construction, because that is
what irrigation is for.

But the model's year-to-year signal is not absent - it has moved. Modelled rice
irrigation at Sangrur ranges from 895 to 2,338 mm across those same seasons. In an
irrigated system the weather shows up in **water demand**, not in yield. So the
thing to validate is the water, and the aquifer is the instrument that recorded it:
every cubic metre pumped out of a closed basin shows up as a falling water table.

CGWB maintains observation wells reporting through India-WRIS. Depth to water is
reported as a negative number of metres below ground, so a *decreasing* value is a
*falling* table.

Two implementation notes, both deliberate:

1. **Transport is curl, not urllib.** indiawris.gov.in serves an incomplete
   certificate chain - only the leaf, with the Sectigo intermediate missing - so
   Python's verification fails against certifi while curl succeeds by resolving the
   issuer through the system trust store. The alternative would have been to
   disable verification, which is never acceptable for a dataset we intend to make
   claims from. Verification here is full; it is simply delegated to a client whose
   trust store can complete the chain.

2. **A station is only usable if it has a long enough record.** A well installed in
   2019 cannot tell you anything about a twenty-year trend, and averaging it in
   with long-running wells would silently bias the trend toward recent years. The
   screening is explicit and reported.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# The path really does contain spaces; they must be percent-encoded or curl
# rejects the URL outright.
WRIS_URL = "https://indiawris.gov.in/Dataset/Ground%20Water%20Level"

# A station needs this many distinct years before a trend through it means anything.
MIN_YEARS_FOR_TREND = 8

# Depth to water below ground level, metres, AFTER normalising the sign. Outside
# this range the value is a sentinel or a unit problem rather than a water level.
PLAUSIBLE_DEPTH_M = (0.2, 200.0)

# The sign convention is NOT consistent across acquisition modes, and this is the
# single most dangerous thing about this dataset. At Sangrur:
#
#     Manual wells      depth reported POSITIVE below ground   (4.7 .. 56.6 m)
#     Telemetric wells  depth reported NEGATIVE below ground  (-192 .. +47 m)
#
# Mixing them without normalising does not produce a noisy trend, it produces a
# meaningless one - half the stations would move the opposite way from the other
# half for the same physical behaviour. Normalising to "positive metres below
# ground" is done per mode, explicitly, rather than by taking an absolute value,
# because abs() would also silently swallow genuinely artesian readings instead of
# rejecting them.
DEPTH_SIGN = {"Manual": 1.0, "Telemetric": -1.0}


class IndiaWrisError(ValueError):
    pass


@dataclass(frozen=True)
class WellSeries:
    """One observation well's annual mean depth to water."""

    station_code: str
    station_name: str
    block: str
    latitude: float
    longitude: float
    years: np.ndarray          # int
    depth_m: np.ndarray        # metres BELOW ground, positive down (normalised)
    n_readings: int

    @property
    def span_years(self) -> int:
        return int(self.years.max() - self.years.min() + 1) if len(self.years) else 0

    @property
    def trend_m_per_year(self) -> float:
        """Least-squares slope of depth-below-ground against year.

        POSITIVE means the water table is FALLING, because depth is normalised to
        metres below ground. This is the quantity the Punjab validation turns on.
        """
        if len(self.years) < 2:
            return float("nan")
        return float(np.polyfit(self.years.astype(float), self.depth_m, 1)[0])

    def distance_km(self, latitude: float, longitude: float) -> float:
        """Great-circle distance, which is what decides whether a well is local."""
        r = 6371.0
        p1, p2 = np.radians(latitude), np.radians(self.latitude)
        dp = p2 - p1
        dl = np.radians(self.longitude - longitude)
        a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
        return float(2 * r * np.arcsin(np.sqrt(a)))



PAGE_SIZE = 20000


def _last_day(year: int, month: int) -> int:
    import calendar
    return calendar.monthrange(year, month)[1]


def _window(
    state: str, district: str, agency: str, start: str, end: str, timeout_s: int
) -> list[dict] | None:
    """One request. Returns None when the service reports no data for the window."""
    form = "&".join([
        f"stateName={state}", f"districtName={district}", f"agencyName={agency}",
        f"startdate={start}", f"enddate={end}",
        "download=false", "page=0", f"size={PAGE_SIZE}",
    ])
    try:
        result = subprocess.run(
            ["curl", "-sS", "--fail", "--max-time", str(timeout_s),
             "-X", "POST", WRIS_URL,
             "-H", "Content-Type: application/x-www-form-urlencoded",
             "-d", form],
            capture_output=True, timeout=timeout_s + 30, check=True,
        )
    except FileNotFoundError as exc:
        raise IndiaWrisError("curl is required for the India-WRIS adapter") from exc
    except subprocess.CalledProcessError as exc:
        raise IndiaWrisError(
            f"India-WRIS refused {start}..{end}: {exc.stderr.decode()[:200]}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise IndiaWrisError(f"India-WRIS did not answer for {start}..{end}") from exc

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise IndiaWrisError(f"India-WRIS returned non-JSON for {start}: {exc}") from exc

    message = str(payload.get("message", ""))
    if payload.get("statusCode") != 200:
        # A window the wells were simply not read in is empty, not a failure.
        # Anything else is a real error and must not pass silently.
        if "no data found" in message.lower():
            return None
        raise IndiaWrisError(f"India-WRIS said for {start}..{end}: {message!r}")
    return payload.get("data") or []


def fetch_groundwater(
    state: str,
    district: str,
    *,
    start: str = "1994-01-01",
    end: str = "2024-12-31",
    agency: str = "CGWB",
    cache_path: str | None = None,
    timeout_s: int = 180,
) -> list[dict]:
    """Raw observation-well readings for one district.

    Transport is curl; see the module docstring for why. Verification is full - no
    ``-k``, no ``--insecure``.
    """
    if cache_path:
        path = Path(cache_path)
        if path.exists():
            return json.loads(path.read_text())["data"] or []

    # Fetch one year at a time. The endpoint caps a response at 50,000 rows, and
    # the telemetric piezometers installed from the 2000s report every few minutes
    # - a single 1994-2024 request came back entirely full of GPRS readings from
    # 20 stations, with every manual well silently crowded out. Year-by-year keeps
    # each response far below the cap so nothing is lost to truncation.
    first_year, last_year = int(start[:4]), int(end[:4])
    rows: list[dict] = []
    for year in range(first_year, last_year + 1):
        window = _window(state, district, agency,
                         f"{year}-01-01", f"{year}-12-31", timeout_s)
        if window is None:
            continue
        if len(window) >= PAGE_SIZE:
            # Hitting the cap means the response is truncated, and a silently
            # truncated year bends the trend line. Recent years can carry hundreds
            # of thousands of telemetric readings, so split the year into months
            # and try again rather than accept a partial answer.
            for month in range(1, 13):
                last_day = _last_day(year, month)
                chunk = _window(state, district, agency,
                                f"{year}-{month:02d}-01",
                                f"{year}-{month:02d}-{last_day}", timeout_s)
                if chunk is None:
                    continue
                if len(chunk) >= PAGE_SIZE:
                    raise IndiaWrisError(
                        f"{year}-{month:02d} still returned {len(chunk)} rows at the "
                        "page cap; the series for this district cannot be fetched "
                        "completely and would be wrong if used."
                    )
                rows.extend(chunk)
        else:
            rows.extend(window)

    if cache_path:
        p = Path(cache_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"statusCode": 200, "data": rows}))
    return rows


def annual_series(
    rows: list[dict], *, min_years: int = MIN_YEARS_FOR_TREND,
    mode: str | None = "Manual",
) -> tuple[list[WellSeries], dict[str, int]]:
    """Collapse readings to annual means per station, and screen short records.

    Annual means rather than raw readings because CGWB wells are read
    pre-monsoon and post-monsoon, and the seasonal swing in a pumped aquifer is
    larger than the annual trend - mixing them would put the seasonal cycle into
    the trend. The screening tally is returned rather than printed so a caller can
    report how many stations were dropped and why.
    """
    by_station: dict[str, list[dict]] = {}
    rejected = {"no value": 0, "implausible depth": 0, "wrong acquisition mode": 0}
    for row in rows:
        # Manual wells only. The telemetric piezometers report every few minutes
        # and their record starts in the 2000s, so mixing them in would both swamp
        # the annual means and shorten the usable span. They are also noisier: the
        # telemetric values at Sangrur include readings above ground level, which
        # a depth-to-water series cannot contain.
        if mode is not None and row.get("dataAcquisitionMode") != mode:
            rejected["wrong acquisition mode"] += 1
            continue
        value = row.get("dataValue")
        if value is None:
            rejected["no value"] += 1
            continue
        sign = DEPTH_SIGN.get(row.get("dataAcquisitionMode"))
        if sign is None:
            rejected["unknown sign convention"] = (
                rejected.get("unknown sign convention", 0) + 1
            )
            continue
        depth = sign * float(value)
        if not PLAUSIBLE_DEPTH_M[0] <= depth <= PLAUSIBLE_DEPTH_M[1]:
            rejected["implausible depth"] += 1
            continue
        by_station.setdefault(row["stationCode"], []).append(
            {**row, "_depth_m": depth}
        )

    series: list[WellSeries] = []
    rejected["record too short"] = 0
    for code, readings in by_station.items():
        years: dict[int, list[float]] = {}
        for row in readings:
            years.setdefault(int(row["dataTime"][:4]), []).append(row["_depth_m"])
        if len(years) < min_years:
            rejected["record too short"] += 1
            continue
        ordered = sorted(years)
        first = readings[0]
        series.append(WellSeries(
            station_code=code,
            station_name=first.get("stationName", code),
            block=first.get("block") or "",
            latitude=float(first["latitude"]),
            longitude=float(first["longitude"]),
            years=np.array(ordered, dtype=int),
            depth_m=np.array([float(np.mean(years[y])) for y in ordered]),
            n_readings=len(readings),
        ))
    series.sort(key=lambda s: s.station_code)
    return series, rejected


def district_decline_m_per_year(series: list[WellSeries]) -> tuple[float, float, int]:
    """Median, inter-quartile spread, and count of per-well trends, m/yr.

    Positive means the water table is falling.

    The median rather than the mean, because a single well sited next to a canal or
    a heavy pumping centre is not representative and an outlier should not move the
    district figure. The spread is returned with it: a district where wells disagree
    about the sign of the trend has not told you anything, and reporting a tidy
    median alone would hide that.
    """
    trends = np.array([s.trend_m_per_year for s in series])
    trends = trends[np.isfinite(trends)]
    if not len(trends):
        raise IndiaWrisError("no well has enough readings to fit a trend")
    q1, q3 = np.percentile(trends, [25, 75])
    return float(np.median(trends)), float(q3 - q1), int(len(trends))
