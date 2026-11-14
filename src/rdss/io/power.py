"""NASA POWER adapter: the first real dataset behind the weather contract.

POWER is the right first adapter for three reasons. It needs no authentication, it
is a plain REST/JSON endpoint rather than a granule order, and it supplies every
variable the contract asks for - maximum and minimum temperature, relative
humidity, 2 m wind, downward shortwave radiation and corrected precipitation. That
last point matters more than convenience: with all six present, ET0 is computed by
full Penman-Monteith with no substitutions, so the result carries high confidence
rather than the degraded rating a temperature-only source forces.

The module is deliberately split. `parse_power_daily` is pure and takes a payload,
so it is fully testable offline against recorded responses, including their fill
values and unit variants. `fetch_power_daily` is a thin network call with on-disk
caching. Gap filling lives here rather than in the model layer, because this is
where the method can be recorded - which the contract requires.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..agro.climate import DailyWeather, Site, WeatherError

POWER_DAILY_POINT_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

# POWER parameter -> our contract variable.
PARAMETER_MAP: dict[str, str] = {
    "T2M_MAX": "tmax_c",
    "T2M_MIN": "tmin_c",
    "PRECTOTCORR": "precip_mm",
    "RH2M": "rh_mean_pct",
    "WS2M": "wind_2m_ms",
    "ALLSKY_SFC_SW_DWN": "solar_mj_m2",
}
REQUIRED_PARAMETERS = ("T2M_MAX", "T2M_MIN", "PRECTOTCORR")
DEFAULT_FILL_VALUE = -999.0

# Unit strings POWER has been observed to return, normalised to lower case with
# spaces stripped, mapped to (multiplier, offset) onto our contract units.
UNIT_CONVERSIONS: dict[tuple[str, str], tuple[float, float]] = {
    ("tmax_c", "c"): (1.0, 0.0),
    ("tmax_c", "k"): (1.0, -273.15),
    ("tmin_c", "c"): (1.0, 0.0),
    ("tmin_c", "k"): (1.0, -273.15),
    ("precip_mm", "mm/day"): (1.0, 0.0),
    ("precip_mm", "mm"): (1.0, 0.0),
    ("rh_mean_pct", "%"): (1.0, 0.0),
    ("wind_2m_ms", "m/s"): (1.0, 0.0),
    # Radiation is the one that genuinely varies between POWER endpoints.
    ("solar_mj_m2", "mj/m^2/day"): (1.0, 0.0),
    ("solar_mj_m2", "mj/m2/day"): (1.0, 0.0),
    ("solar_mj_m2", "kw-hr/m^2/day"): (3.6, 0.0),
    ("solar_mj_m2", "kwh/m^2/day"): (3.6, 0.0),
    ("solar_mj_m2", "w/m^2"): (0.0864, 0.0),
    ("solar_mj_m2", "w/m2"): (0.0864, 0.0),
}


class PowerError(ValueError):
    """The POWER response could not be turned into a valid weather series."""


@dataclass(frozen=True)
class FillRecord:
    variable: str
    filled_days: int
    total_days: int
    longest_gap_days: int
    method: str

    @property
    def fraction(self) -> float:
        return self.filled_days / self.total_days if self.total_days else 0.0


@dataclass(frozen=True)
class PowerReport:
    """What the adapter did, in enough detail to put on an evidence card."""

    site: Site
    requested_start: str
    requested_end: str
    days: int
    api_version: str
    fill_value: float
    variables_present: tuple[str, ...]
    variables_absent: tuple[str, ...]
    fills: tuple[FillRecord, ...]
    unit_conversions: tuple[str, ...]
    messages: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total_filled_days(self) -> int:
        return sum(f.filled_days for f in self.fills)

    @property
    def confidence(self) -> str:
        """Degrades with how much of the record had to be reconstructed."""
        if not self.fills:
            return "high"
        worst = max(f.fraction for f in self.fills)
        longest = max(f.longest_gap_days for f in self.fills)
        if worst > 0.05 or longest > 7:
            return "low"
        if worst > 0.01 or longest > 3:
            return "medium"
        return "high"

    def describe(self) -> str:
        lines = [
            f"NASA POWER daily point, API {self.api_version}, "
            f"{self.days} days {self.requested_start}..{self.requested_end} at "
            f"{self.site.latitude_deg:.4f}, {self.site.longitude_deg:.4f}",
            f"variables: {', '.join(self.variables_present) or 'none'}",
        ]
        if self.variables_absent:
            lines.append(f"absent: {', '.join(self.variables_absent)}")
        for f in self.fills:
            lines.append(
                f"gap-filled {f.variable}: {f.filled_days}/{f.total_days} days "
                f"({f.fraction:.2%}), longest run {f.longest_gap_days} d, by {f.method}"
            )
        for c in self.unit_conversions:
            lines.append(f"unit conversion: {c}")
        for m in self.messages:
            lines.append(f"POWER message: {m}")
        lines.append(f"record confidence: {self.confidence}")
        return "\n".join(lines)


def _normalise_unit(unit: str) -> str:
    return unit.strip().lower().replace(" ", "")


def _interpolate(values: np.ndarray, missing: np.ndarray) -> tuple[np.ndarray, int]:
    """Linear interpolation across interior gaps, nearest value at the ends.

    Returns the filled array and the longest consecutive gap, so a caller can refuse
    a record that was reconstructed across too long a stretch.
    """
    out = values.copy()
    if not missing.any():
        return out, 0

    index = np.arange(len(values))
    good = ~missing
    if not good.any():
        raise PowerError("every value is a fill value; nothing to interpolate from")
    out[missing] = np.interp(index[missing], index[good], values[good])

    longest = run = 0
    for m in missing:
        run = run + 1 if m else 0
        longest = max(longest, run)
    return out, longest


def parse_power_daily(
    payload: dict,
    *,
    site: Site | None = None,
    fill_strategy: str = "interpolate",
    max_gap_days: int = 10,
    max_filled_fraction: float = 0.10,
) -> tuple[DailyWeather, PowerReport]:
    """Turn a POWER daily-point JSON payload into a validated weather series.

    Pure: no network, no clock, no filesystem. Everything the adapter decides is
    recorded in the returned report rather than applied silently.
    """
    if fill_strategy not in ("interpolate", "fail"):
        raise PowerError(f"unknown fill strategy {fill_strategy!r}")

    try:
        parameters = payload["properties"]["parameter"]
    except (KeyError, TypeError):
        raise PowerError(
            "payload has no properties.parameter block; this does not look like a "
            "POWER daily-point response"
        ) from None
    if not parameters:
        raise PowerError("POWER returned no parameters")

    header = payload.get("header", {}) or {}
    units_block = payload.get("parameters", {}) or {}
    fill_value = float(header.get("fill_value", DEFAULT_FILL_VALUE))
    api_version = str((header.get("api") or {}).get("version", "unknown"))
    messages = tuple(str(m) for m in (payload.get("messages") or []))

    for required in REQUIRED_PARAMETERS:
        if required not in parameters:
            raise PowerError(
                f"POWER response is missing {required}, which the weather contract "
                "requires. Request it explicitly in the `parameters` query argument."
            )

    # Build a gap-free date axis from the union of all returned day keys, so a day
    # POWER omits entirely becomes an explicit gap rather than a silent shortening.
    all_keys: set[str] = set()
    for series in parameters.values():
        all_keys.update(series)
    try:
        day_numbers = sorted(np.datetime64(f"{k[:4]}-{k[4:6]}-{k[6:8]}", "D") for k in all_keys)
    except (ValueError, IndexError) as exc:
        raise PowerError(f"unparseable date key in POWER response: {exc}") from None
    if not day_numbers:
        raise PowerError("POWER response contains no dates")

    dates = np.arange(day_numbers[0], day_numbers[-1] + np.timedelta64(1, "D"))
    n = len(dates)
    keys = [str(d).replace("-", "") for d in dates]

    resolved_site = site or _site_from_payload(payload)

    arrays: dict[str, np.ndarray] = {}
    fills: list[FillRecord] = []
    conversions: list[str] = []
    present: list[str] = []
    absent: list[str] = []

    for power_name, our_name in PARAMETER_MAP.items():
        series = parameters.get(power_name)
        if series is None:
            absent.append(power_name)
            continue

        raw = np.array([series.get(k, fill_value) for k in keys], dtype=float)
        missing = ~np.isfinite(raw) | np.isclose(raw, fill_value, atol=1e-6)
        # POWER has used both -999 and -99 historically; treat any sentinel at or
        # below the declared fill value as missing rather than as a real reading.
        missing |= raw <= fill_value + 1e-6

        if missing.all():
            absent.append(power_name)
            continue

        unit = _normalise_unit(str((units_block.get(power_name) or {}).get("units", "")))
        if unit:
            try:
                scale, offset = UNIT_CONVERSIONS[(our_name, unit)]
            except KeyError:
                raise PowerError(
                    f"POWER returned {power_name} in unrecognised units {unit!r}; "
                    "refusing to guess a conversion"
                ) from None
            if (scale, offset) != (1.0, 0.0):
                raw = np.where(missing, raw, raw * scale + offset)
                conversions.append(f"{power_name} {unit} -> contract units")

        if missing.any():
            if fill_strategy == "fail":
                raise PowerError(
                    f"{power_name} has {int(missing.sum())} missing day(s) and "
                    "fill_strategy='fail'"
                )
            raw, longest = _interpolate(raw, missing)
            record = FillRecord(
                variable=our_name,
                filled_days=int(missing.sum()),
                total_days=n,
                longest_gap_days=longest,
                method="linear interpolation, nearest value at series ends",
            )
            if longest > max_gap_days:
                raise PowerError(
                    f"{power_name} has a {longest}-day gap, beyond max_gap_days="
                    f"{max_gap_days}. Interpolating across it would invent weather; "
                    "shorten the request or source this period elsewhere."
                )
            if record.fraction > max_filled_fraction:
                raise PowerError(
                    f"{power_name} is {record.fraction:.1%} gap-filled, beyond "
                    f"max_filled_fraction={max_filled_fraction:.0%}"
                )
            fills.append(record)

        arrays[our_name] = raw
        present.append(power_name)

    # Precipitation sentinels and tiny negative noise both become zero; a negative
    # rainfall would fail the contract for the wrong reason.
    if "precip_mm" in arrays:
        arrays["precip_mm"] = np.maximum(arrays["precip_mm"], 0.0)

    provenance = {
        our: f"NASA POWER {power} (API {api_version})"
        for power, our in PARAMETER_MAP.items()
        if our in arrays
    }

    try:
        weather = DailyWeather(dates=dates, provenance=provenance, **arrays)
    except WeatherError as exc:
        raise PowerError(f"POWER data failed the weather contract: {exc}") from None

    report = PowerReport(
        site=resolved_site,
        requested_start=str(header.get("start", keys[0])),
        requested_end=str(header.get("end", keys[-1])),
        days=n,
        api_version=api_version,
        fill_value=fill_value,
        variables_present=tuple(present),
        variables_absent=tuple(absent),
        fills=tuple(fills),
        unit_conversions=tuple(conversions),
        messages=messages,
    )
    return weather, report


def _site_from_payload(payload: dict) -> Site:
    """Recover the site from the response geometry, including elevation if given."""
    coords = ((payload.get("geometry") or {}).get("coordinates")) or []
    if len(coords) < 2:
        raise PowerError(
            "POWER response has no usable geometry; pass `site` explicitly so "
            "elevation is known (it sets atmospheric pressure in ET0)"
        )
    lon, lat = float(coords[0]), float(coords[1])
    elevation = float(coords[2]) if len(coords) > 2 else 0.0
    return Site(latitude_deg=lat, longitude_deg=lon, elevation_m=elevation)


# ---------------------------------------------------------------- network


def power_request_url(
    site: Site,
    start: str,
    end: str,
    parameters: tuple[str, ...] = tuple(PARAMETER_MAP),
    community: str = "AG",
) -> str:
    """The exact URL that will be requested. Returned so it can be logged or cited."""
    query = urllib.parse.urlencode(
        {
            "parameters": ",".join(parameters),
            "community": community,
            "longitude": f"{site.longitude_deg:.5f}",
            "latitude": f"{site.latitude_deg:.5f}",
            "start": start.replace("-", ""),
            "end": end.replace("-", ""),
            "format": "JSON",
        }
    )
    return f"{POWER_DAILY_POINT_URL}?{query}"


def fetch_power_daily(
    site: Site,
    start: str,
    end: str,
    *,
    parameters: tuple[str, ...] = tuple(PARAMETER_MAP),
    community: str = "AG",
    cache_path: str | Path | None = None,
    timeout_s: float = 60.0,
) -> dict:
    """Fetch a POWER daily-point payload, caching it on disk.

    Caching is not an optimisation here, it is a working practice: a hackathon
    cannot afford to re-request a 40-year series, and a cached payload makes a run
    reproducible after the fact.
    """
    if cache_path is not None:
        cache = Path(cache_path)
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))

    url = power_request_url(site, start, end, parameters, community)
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise PowerError(f"POWER returned HTTP {exc.code} for {url}") from None
    except urllib.error.URLError as exc:
        raise PowerError(f"could not reach POWER: {exc.reason}") from None

    if cache_path is not None:
        cache = Path(cache_path)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def load_power_daily(
    site: Site, start: str, end: str, **kw
) -> tuple[DailyWeather, PowerReport]:
    """Fetch and parse in one call. Parse options are forwarded."""
    parse_keys = {"fill_strategy", "max_gap_days", "max_filled_fraction"}
    parse_kw = {k: v for k, v in kw.items() if k in parse_keys}
    fetch_kw = {k: v for k, v in kw.items() if k not in parse_keys}
    payload = fetch_power_daily(site, start, end, **fetch_kw)
    return parse_power_daily(payload, site=site, **parse_kw)
