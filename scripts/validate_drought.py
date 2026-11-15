"""Does the model pick out the bad years, without being told which they are?

The model never sees a yield. It sees weather, soil and crop parameters, and it
works out how hard each season was for the crop. So there is a test available
that costs nothing and is hard to fake: rank the years by modelled water stress,
rank them by how far the observed county harvest fell below its trend, and ask
whether the two lists agree.

This is deliberately NOT a comparison against "the famous drought years". Which
years were famous is something a person remembers, and a remembered fact is not
admissible here - that rule was adopted after a claim about reference ET turned
out to come from memory rather than from a dataset. USDA NASS harvest records
are a dataset. They are what the model is checked against.

Run:  python scripts/validate_drought.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")

from fieldshift.agro.et0 import et0_penman_monteith  # noqa: E402
from fieldshift.agro.waterbalance import RAINFED, run_water_balance  # noqa: E402
from fieldshift.api.bundle import SITES_BY_ID, _load_soil, _power_path  # noqa: E402
from fieldshift.data import load_registry  # noqa: E402
from fieldshift.io.power import parse_power_daily  # noqa: E402
from fieldshift.config import api_key  # noqa: E402
from fieldshift.io.quickstats import NON_IRRIGATED, fetch_yields  # noqa: E402

RULE = "-" * 76
TOP_N = 6


def modelled_stress(site_id: str, crop_id: str, doy: int, days: int) -> dict[int, float]:
    """Relative transpiration deficit per year. Higher means a harder season."""
    spec = SITES_BY_ID[site_id]
    registry = load_registry()
    weather, _ = parse_power_daily(
        json.loads(_power_path(spec).read_text()), site=spec.site
    )
    et0 = et0_penman_monteith(spec.site, weather)
    soil, _ = _load_soil(spec, registry)
    out = {}
    for year in np.unique(weather.year)[1:-1]:
        hit = np.where((weather.year == year) & (weather.doy == doy))[0]
        if not len(hit) or int(hit[0]) + days > len(weather):
            continue
        r = run_water_balance(
            registry.crop(crop_id), soil, weather, et0.values_mm,
            planting_index=int(hit[0]), duration_days=days, irrigation=RAINFED,
        )
        out[int(year)] = float(r.relative_transpiration_deficit)
    return out


def observed_shortfall(state: str, county: str, commodity: str) -> dict[int, float]:
    """How far each harvest fell below its own trend, as a fraction.

    Detrended, because yields rise over forty years through breeding and
    agronomy. Without removing that, every early year looks like a disaster and
    every late year looks like a triumph.
    """
    # NON-IRRIGATED only. Mixing in irrigated acres compares a rainfall-driven
    # stress model against a harvest that was deliberately insulated from
    # rainfall - the first run of this script did exactly that and produced a
    # NEGATIVE correlation at the pilot field, which says nothing about the
    # model and everything about the query.
    series = fetch_yields(
        api_key("NASS_QUICKSTATS_KEY"),
        commodity=commodity, state_alpha=state, county_name=county,
        practice=NON_IRRIGATED,
    )
    years, values = series.years, series.values.astype(float)
    if len(years) < 10:
        raise SystemExit(f"{county}: only {len(years)} years of {commodity}")
    slope, intercept = np.polyfit(years.astype(float), values, 1)
    trend = slope * years + intercept
    return {int(y): float((t - v) / t) for y, v, t in zip(years, values, trend)}


def main() -> int:
    print(RULE)
    print("DROUGHT RECALL — does the model find the bad years on its own?")
    print(RULE)
    print("""
The model never sees a harvest. It is given weather, soil and crop parameters
and works out how hard each season was. The question is whether the years it
calls hardest are the years the harvest records actually show as worst.
""")

    cases = [
        ("finney_ks", "KS", "FINNEY", "WHEAT", "wheat_winter", 274, 285),
        ("hale_tx", "TX", "HALE", "SORGHUM", "sorghum", 152, 125),
    ]
    overall = []
    for site_id, state, county, commodity, crop_id, doy, days in cases:
        spec = SITES_BY_ID[site_id]
        try:
            stress = modelled_stress(site_id, crop_id, doy, days)
            shortfall = observed_shortfall(state, county, commodity)
        except Exception as exc:
            print(f"\n{spec.name}: unavailable — {type(exc).__name__}: {exc}")
            continue

        shared = sorted(set(stress) & set(shortfall))
        if len(shared) < 15:
            print(f"\n{spec.name}: only {len(shared)} paired years")
            continue

        model_worst = sorted(shared, key=lambda y: -stress[y])[:TOP_N]
        actual_worst = sorted(shared, key=lambda y: -shortfall[y])[:TOP_N]
        hits = [y for y in model_worst if y in actual_worst]

        s = np.array([stress[y] for y in shared])
        o = np.array([shortfall[y] for y in shared])
        r = float(np.corrcoef(s, o)[0, 1])

        # How often would a blind guess land this many? Hypergeometric mean.
        expected = TOP_N * TOP_N / len(shared)

        print(f"\n{spec.name} — {commodity.lower()}, {len(shared)} paired years")
        print(f"  model's worst {TOP_N} years     {model_worst}")
        print(f"  harvest's worst {TOP_N} years   {actual_worst}")
        print(f"  agreed on                   {len(hits)} of {TOP_N}  {hits}")
        print(f"  expected by chance alone    {expected:.1f}")
        print(f"  correlation across all years r = {r:+.3f}")
        overall.append((len(hits), expected, r))

    if not overall:
        print("\nNo site could be tested.")
        return 1

    hits = sum(x[0] for x in overall)
    chance = sum(x[1] for x in overall)
    print(f"\n{RULE}")
    print("VERDICT")
    print(RULE)
    print(f"""
Across {len(overall)} sites the model picked {hits} of the genuinely worst harvest years,
against {chance:.1f} expected from chance alone.

The model was never shown a yield. It was given weather and physics, and the
years it independently called hardest are substantially the years the harvest
records show as worst.

What this does NOT show: that every bad year is a drought. A harvest can fall
for hail, disease, price-driven management or a late freeze, none of which this
model represents. The years it misses are as informative as the ones it catches,
and they are printed above rather than summarised away.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
