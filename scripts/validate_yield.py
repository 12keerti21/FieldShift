"""The headline validation: does our physics reproduce four decades of observed yield?

Our model never sees a yield. It takes NASA POWER weather, SSURGO soil and FAO-56
crop parameters and produces a relative transpiration deficit - the fraction of a
crop's water demand that went unmet. FAO-33 maps that onto yield loss through a
crop-specific response factor Ky:

    1 - Ya/Ym  =  Ky x (1 - ETa/ETm)

Everything on the left is observed by USDA NASS. Everything on the right is
computed. That makes this a genuine out-of-sample test rather than a fit.

Two cautions are built in. County yields carry a large technology trend that has
nothing to do with weather, so the comparison is against DETRENDED yield. And the
paired-year counts are small - 19 to 25 - so p-values are reported alongside every
correlation.

    PYTHONPATH=src ./.venv/bin/python scripts/validate_yield.py
"""

from __future__ import annotations

import json
import sys

import numpy as np

from fieldshift.agro.climate import Site
from fieldshift.agro.et0 import et0_penman_monteith
from fieldshift.agro.waterbalance import IrrigationPolicy, RAINFED, run_water_balance
from fieldshift.config import api_key
from fieldshift.data import load_registry
from fieldshift.io.power import parse_power_daily
from fieldshift.io.quickstats import (
    IRRIGATED,
    NON_IRRIGATED,
    NON_IRRIGATED_AFTER_FALLOW,
    NON_IRRIGATED_CONTINUOUS,
    QuickStatsError,
    fetch_yields,
    skill,
)
from fieldshift.io.ssurgo import fetch_ssurgo_point, texture_from_ssurgo

SITE = Site(38.03, -100.74, 858.0, "Finney County, Kansas")

# FAO-33 Table (Doorenbos & Kassam 1979) yield response factors, whole growing
# season. Only the crops this county reports.
KY = {"maize": 1.25, "sorghum": 0.90, "wheat": 1.05}

PIVOT = IrrigationPolicy(
    mode="refill_on_stress", efficiency=0.85,
    refill_to_depletion_frac=0.25, max_application_mm=25,
)
PLANTING_DOY = {"maize": 125, "sorghum": 152, "wheat": 274}
RESIDUE = 0.4  # moderate residue cover; the county is mixed till


def load_forcing():
    payload = json.loads(open("data/raw/power_finney_ks_1981_2024.json").read())
    for series in payload["properties"]["parameter"].values():
        for k in list(series):
            if k < "19840101":
                del series[k]
    weather, _ = parse_power_daily(payload, site=SITE)
    return weather, et0_penman_monteith(SITE, weather).values_mm


def deficit_by_year(weather, et0, reg, soil, crop_id, policy, duration=None):
    crop = reg.crop(crop_id)
    dur = duration or min(crop.duration_days, 170)
    out = {}
    for y in np.unique(weather.year):
        idx = np.flatnonzero((weather.year == y) & (weather.doy == PLANTING_DOY[crop_id]))
        if not len(idx) or int(idx[0]) + dur > len(weather):
            continue
        r = run_water_balance(
            crop, soil, weather, et0, planting_index=int(idx[0]), duration_days=dur,
            irrigation=policy, initial_root_depletion_frac=0.2,
            initial_surface_depletion_frac=0.6, residue_cover_fraction=RESIDUE,
        )
        # Winter wheat sown in autumn is harvested the following calendar year,
        # which is the year NASS reports it under.
        harvest_year = int(y) + 1 if crop_id == "wheat" else int(y)
        out[harvest_year] = r.relative_transpiration_deficit
    return out


def report(label, observed, modelled, ky):
    years = np.array([y for y in observed.years if y in modelled])
    if len(years) < 5:
        print(f"  {label:46s} only {len(years)} paired years - skipped")
        return None
    mask = np.isin(observed.years, years)
    residual = observed.detrend()[mask]
    deficit = np.array([modelled[int(y)] for y in years])
    predicted_loss = np.clip(ky * deficit, 0.0, 1.0)

    s = skill(-predicted_loss, residual)
    stars = "***" if s["p_value"] < 0.01 else "**" if s["p_value"] < 0.05 else \
            "*" if s["p_value"] < 0.10 else "ns"
    print(f"  {label:46s} n={int(s['n']):3d}  r={s['r']:+.3f}  "
          f"r2={s['r2']:.2f}  p={s['p_value']:.4f} {stars}")
    return {"label": label, "years": years, "deficit": deficit,
            "predicted_loss": predicted_loss, "observed": observed, "skill": s}


def main() -> int:
    reg = load_registry()
    weather, et0 = load_forcing()
    profile = fetch_ssurgo_point(
        SITE.latitude_deg, SITE.longitude_deg,
        cache_path="data/raw/ssurgo_finney_point.json",
    )
    soil = texture_from_ssurgo(profile, reg.texture)
    key = api_key("NASS_QUICKSTATS_KEY")

    print("HEADLINE VALIDATION - modelled water stress vs observed county yield")
    print(f"{SITE.name}  |  soil: {soil.source}")
    print("Model inputs: NASA POWER weather, SSURGO soil, FAO-56 crop parameters.")
    print("The model never sees a yield. FAO-33 Ky maps stress onto yield loss.\n")

    print("--- 1. Year-to-year skill against DETRENDED county yield ---")
    cases = [
        ("maize", IRRIGATED, PIVOT, "corn, irrigated"),
        ("maize", NON_IRRIGATED, RAINFED, "corn, dryland"),
        ("sorghum", IRRIGATED, PIVOT, "sorghum, irrigated"),
        ("sorghum", NON_IRRIGATED, RAINFED, "sorghum, dryland"),
        ("wheat", NON_IRRIGATED, RAINFED, "wheat, dryland"),
    ]
    kept = []
    for crop_id, practice, policy, label in cases:
        try:
            obs = fetch_yields(
                key, commodity={"maize": "CORN", "sorghum": "SORGHUM",
                                "wheat": "WHEAT"}[crop_id],
                state_alpha="KS", county_name="FINNEY", practice=practice,
            )
        except QuickStatsError as e:
            print(f"  {label:46s} unavailable: {e}")
            continue
        mod = deficit_by_year(weather, et0, reg, soil, crop_id, policy)
        res = report(label, obs, mod, KY[crop_id])
        if res:
            kept.append(res)

    print("\n--- 2. Level test: does the dryland yield PENALTY come out right? ---")
    print("  A harder test than correlation: absolute magnitude, not just co-variation.")
    for crop_id, commodity in (("maize", "CORN"), ("sorghum", "SORGHUM")):
        try:
            irr = fetch_yields(key, commodity=commodity, state_alpha="KS",
                               county_name="FINNEY", practice=IRRIGATED)
            dry = fetch_yields(key, commodity=commodity, state_alpha="KS",
                               county_name="FINNEY", practice=NON_IRRIGATED)
        except QuickStatsError as e:
            print(f"  {commodity}: {e}")
            continue
        shared = irr.common_years(dry)
        if len(shared) < 5:
            continue
        i = irr.values[np.isin(irr.years, shared)].mean()
        d = dry.values[np.isin(dry.years, shared)].mean()
        observed_penalty = 1.0 - d / i

        mod_dry = deficit_by_year(weather, et0, reg, soil, crop_id, RAINFED)
        mod_irr = deficit_by_year(weather, et0, reg, soil, crop_id, PIVOT)
        yrs = [int(y) for y in shared if int(y) in mod_dry and int(y) in mod_irr]
        extra = np.mean([mod_dry[y] - mod_irr[y] for y in yrs])
        predicted_penalty = min(KY[crop_id] * extra, 1.0)
        print(f"  {commodity:9s} observed {observed_penalty:5.1%}   "
              f"predicted {predicted_penalty:5.1%}   "
              f"error {predicted_penalty - observed_penalty:+5.1%}   "
              f"(n={len(yrs)} shared years, irrigated {i:.0f} vs dryland {d:.0f} bu/ac)")

    print("\n--- 3. Rotation test: wheat after summer fallow vs continuous ---")
    print("  NASS reports these separately, so the rotation effect is OBSERVED.")
    try:
        after = fetch_yields(key, commodity="WHEAT", state_alpha="KS",
                             county_name="FINNEY", practice=NON_IRRIGATED_AFTER_FALLOW)
        cont = fetch_yields(key, commodity="WHEAT", state_alpha="KS",
                            county_name="FINNEY", practice=NON_IRRIGATED_CONTINUOUS)
        shared = after.common_years(cont)
        a = after.values[np.isin(after.years, shared)].mean()
        c = cont.values[np.isin(cont.years, shared)].mean()
        print(f"  observed: after fallow {a:.1f} vs continuous {c:.1f} bu/ac "
              f"= {a / c - 1:+.1%} advantage to fallow  (n={len(shared)} years)")
        print("  Our model can only reproduce this once rotations are simulated as")
        print("  sequences with soil water carried across the fallow year - the next")
        print("  component to build. Recorded now as the target to hit.")
    except QuickStatsError as e:
        print(f"  unavailable: {e}")

    print("\n--- Interpretation ---")
    if kept:
        best = max(kept, key=lambda k: abs(k["skill"]["r"]))
        print(f"  Strongest signal: {best['label']} (r={best['skill']['r']:+.3f}).")
    print("  Expect weaker skill for irrigated crops: irrigation is designed to")
    print("  break the link between weather and yield, so there is less variance for")
    print("  a water-stress index to explain. A high correlation there would be")
    print("  suspicious rather than reassuring.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
