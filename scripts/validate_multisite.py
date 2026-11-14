"""Does the model work anywhere other than the field it was built on?

Every result so far rests on one point in Finney County, Kansas. That is the
weakest thing about the project: a model tuned, debugged and validated at a single
location has no demonstrated right to be used anywhere else, and a rotation tool
that only works on one field is not a tool.

So the same engine, with no site-specific adjustment of any kind, is run at four
contrasting sites and checked against observed county yield at each:

    Finney, KS    semi-arid High Plains, ~540 mm, Ogallala irrigation
    Sedgwick, KS  wetter eastern Kansas, ~800 mm
    Holt, NE      cooler Nebraska Sandhills, coarse soil
    Hale, TX      hotter southern High Plains, cotton country

Nothing is fitted per site. Soil comes from SSURGO at each point, weather from
POWER, crop parameters from the same table. If skill collapses away from the pilot
field, that is the finding and it needs saying.

    PYTHONPATH=src ./.venv/bin/python scripts/validate_multisite.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np

from fieldshift.agro.climate import Site
from fieldshift.agro.et0 import et0_penman_monteith
from fieldshift.agro.waterbalance import IrrigationPolicy, RAINFED, run_water_balance
from fieldshift.config import api_key
from fieldshift.data import load_registry
from fieldshift.io.power import load_power_daily
from fieldshift.io.quickstats import (
    IRRIGATED,
    NON_IRRIGATED,
    QuickStatsError,
    fetch_yields,
    skill,
)
from fieldshift.io.ssurgo import fetch_ssurgo_point, texture_from_ssurgo

KY = {"maize": 1.25, "sorghum": 0.90, "wheat_winter": 1.05}
COMMODITY = {"maize": "CORN", "sorghum": "SORGHUM", "wheat_winter": "WHEAT"}
PLANTING = {"maize": 125, "sorghum": 152, "wheat_winter": 274}
DURATION = {"maize": 150, "sorghum": 125, "wheat_winter": 285}
PIVOT = IrrigationPolicy(
    mode="refill_on_stress", efficiency=0.85,
    refill_to_depletion_frac=0.25, max_application_mm=25,
)
RESIDUE = 0.4


@dataclass(frozen=True)
class Pilot:
    name: str
    state: str
    county: str
    site: Site
    note: str


SITES = (
    Pilot("Finney, KS", "KS", "FINNEY", Site(38.03, -100.74, 858.0, "Finney"),
          "semi-arid High Plains, Ogallala - the site everything was built on"),
    Pilot("Sedgwick, KS", "KS", "SEDGWICK", Site(37.70, -97.46, 410.0, "Sedgwick"),
          "wetter eastern Kansas"),
    Pilot("Holt, NE", "NE", "HOLT", Site(42.45, -98.78, 610.0, "Holt"),
          "cooler Nebraska Sandhills, coarse soil"),
    Pilot("Hale, TX", "TX", "HALE", Site(34.07, -101.82, 1030.0, "Hale"),
          "hotter southern High Plains"),
)


def deficits(weather, et0, reg, soil, crop_id, policy):
    crop = reg.crop(crop_id)
    out = {}
    for year in np.unique(weather.year):
        idx = np.flatnonzero(
            (weather.year == year) & (weather.doy == PLANTING[crop_id])
        )
        if not len(idx) or int(idx[0]) + DURATION[crop_id] > len(weather):
            continue
        r = run_water_balance(
            crop, soil, weather, et0, planting_index=int(idx[0]),
            duration_days=DURATION[crop_id], irrigation=policy,
            initial_root_depletion_frac=0.2, initial_surface_depletion_frac=0.6,
            residue_cover_fraction=RESIDUE,
        )
        harvest = int(year) + 1 if crop_id == "wheat_winter" else int(year)
        out[harvest] = r.relative_transpiration_deficit
    return out


def main() -> int:
    reg = load_registry()
    key = api_key("NASS_QUICKSTATS_KEY")

    print("MULTI-SITE VALIDATION - the same engine, no per-site adjustment")
    print("=" * 78)

    rows: list[tuple] = []
    penalties: list[tuple] = []

    for pilot in SITES:
        print(f"\n{pilot.name}  ({pilot.note})")
        weather, report = load_power_daily(
            pilot.site, "1984-01-01", "2024-12-31",
            cache_path=f"data/raw/power_{pilot.state}_{pilot.county}.json".lower(),
        )
        et0 = et0_penman_monteith(pilot.site, weather).values_mm
        profile = fetch_ssurgo_point(
            pilot.site.latitude_deg, pilot.site.longitude_deg,
            cache_path=f"data/raw/ssurgo_{pilot.state}_{pilot.county}.json".lower(),
        )
        soil = texture_from_ssurgo(profile, reg.texture)
        rain = np.mean([
            weather.precip_mm[weather.year == y].sum()
            for y in np.unique(weather.year)[1:-1]
        ])
        print(f"  rainfall {rain:.0f} mm/yr   soil {soil.name} "
              f"({profile.component}), {soil.taw_mm_per_m:.0f} mm/m available")

        for crop_id, practice, policy, label in (
            ("maize", NON_IRRIGATED, RAINFED, "corn, dryland"),
            ("sorghum", NON_IRRIGATED, RAINFED, "sorghum, dryland"),
            ("wheat_winter", NON_IRRIGATED, RAINFED, "wheat, dryland"),
            ("maize", IRRIGATED, PIVOT, "corn, irrigated"),
        ):
            try:
                observed = fetch_yields(
                    key, commodity=COMMODITY[crop_id], state_alpha=pilot.state,
                    county_name=pilot.county, practice=practice,
                )
            except QuickStatsError:
                continue
            modelled = deficits(weather, et0, reg, soil, crop_id, policy)
            years = np.array([y for y in observed.years if int(y) in modelled])
            if len(years) < 8:
                continue
            mask = np.isin(observed.years, years)
            residual = observed.detrend()[mask]
            predicted = -np.clip(
                KY[crop_id] * np.array([modelled[int(y)] for y in years]), 0.0, 1.0
            )
            s = skill(predicted, residual)
            stars = ("***" if s["p_value"] < 0.01 else "**" if s["p_value"] < 0.05
                     else "*" if s["p_value"] < 0.10 else "ns")
            print(f"    {label:20s} n={int(s['n']):3d}  r={s['r']:+.3f}  "
                  f"p={s['p_value']:.4f} {stars}")
            rows.append((pilot.name, label, s["r"], s["p_value"], int(s["n"])))

        # Absolute magnitude: the dryland yield penalty.
        try:
            irr = fetch_yields(key, commodity="CORN", state_alpha=pilot.state,
                               county_name=pilot.county, practice=IRRIGATED)
            dry = fetch_yields(key, commodity="CORN", state_alpha=pilot.state,
                               county_name=pilot.county, practice=NON_IRRIGATED)
            shared = irr.common_years(dry)
            if len(shared) >= 8:
                i = irr.values[np.isin(irr.years, shared)].mean()
                d = dry.values[np.isin(dry.years, shared)].mean()
                observed_penalty = 1.0 - d / i
                md = deficits(weather, et0, reg, soil, "maize", RAINFED)
                mi = deficits(weather, et0, reg, soil, "maize", PIVOT)
                yrs = [int(y) for y in shared if int(y) in md and int(y) in mi]
                extra = np.mean([md[y] - mi[y] for y in yrs])
                predicted_penalty = min(KY["maize"] * extra, 1.0)
                print(f"    corn dryland penalty  observed {observed_penalty:5.1%}  "
                      f"predicted {predicted_penalty:5.1%}  "
                      f"error {predicted_penalty - observed_penalty:+5.1%}")
                penalties.append((pilot.name, observed_penalty, predicted_penalty))
        except QuickStatsError:
            pass

    print("\n" + "=" * 78)
    print("SUMMARY - dryland skill against detrended county yield")
    dry = [r for r in rows if "dryland" in r[1]]
    if dry:
        rs = np.array([r[2] for r in dry])
        sig = sum(1 for r in dry if r[3] < 0.05)
        print(f"  {len(dry)} dryland crop-site combinations across {len(SITES)} sites")
        print(f"  mean r {rs.mean():+.3f}   median {np.median(rs):+.3f}   "
              f"range {rs.min():+.3f} to {rs.max():+.3f}")
        print(f"  significant at p<0.05: {sig}/{len(dry)}")
        print(f"  positive skill: {int((rs > 0).sum())}/{len(dry)}")
    irr = [r for r in rows if "irrigated" in r[1]]
    if irr:
        rs = np.array([r[2] for r in irr])
        print(f"\n  irrigated combinations: mean r {rs.mean():+.3f} - near zero is")
        print("  EXPECTED, since irrigation exists to decouple yield from weather")
    if penalties:
        errs = np.array([p[2] - p[1] for p in penalties])
        print(f"\n  dryland corn penalty: mean absolute error "
              f"{np.abs(errs).mean():.1%} across {len(penalties)} sites")
    return 0


if __name__ == "__main__":
    sys.exit(main())
