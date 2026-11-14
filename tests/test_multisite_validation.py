"""The model is checked away from the field it was built on.

Everything in this project was developed, debugged and validated at one point in
Finney County, Kansas. A model tuned at a single location has no demonstrated right
to be used elsewhere, so the same engine - with no per-site adjustment of any kind -
is run at four contrasting sites and checked against observed county yield.

The sites span a 503-841 mm rainfall range and, more importantly, a 2.5-fold range
in soil water-holding capacity: the Nebraska Sandhills site holds 84 mm/m against
the pilot field's 212. If the model only worked on a silt loam, that is where it
would show.

These tests use cached data and assert the skill pattern rather than recomputing it,
so they stay fast. Re-derive the numbers with scripts/validate_multisite.py.
"""

import pathlib

import numpy as np
import pytest

from fieldshift.agro.climate import Site
from fieldshift.agro.et0 import et0_penman_monteith
from fieldshift.agro.waterbalance import RAINFED, IrrigationPolicy, run_water_balance
from fieldshift.io.power import parse_power_daily
from fieldshift.io.quickstats import NON_IRRIGATED, parse_yield_rows, skill
from fieldshift.io.ssurgo import fetch_ssurgo_point, texture_from_ssurgo

import json

DATA = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw"

SITES = {
    "finney": (Site(38.03, -100.74, 858.0), "ks", "finney", 212.0),
    "sedgwick": (Site(37.70, -97.46, 410.0), "ks", "sedgwick", 200.0),
    "holt": (Site(42.45, -98.78, 610.0), "ne", "holt", 84.0),
    "hale": (Site(34.07, -101.82, 1030.0), "tx", "hale", 178.0),
}


def _have(state, county):
    return (DATA / f"power_{state}_{county}.json").exists() and (
        DATA / f"ssurgo_{state}_{county}.json"
    ).exists()


pytestmark = pytest.mark.skipif(
    not all(_have(s, c) for _, s, c, _ in SITES.values()),
    reason="multi-site cache not present; run scripts/validate_multisite.py",
)


def _load(reg, key):
    site, state, county, _ = SITES[key]
    payload = json.loads((DATA / f"power_{state}_{county}.json").read_text())
    weather, _ = parse_power_daily(payload, site=site)
    et0 = et0_penman_monteith(site, weather).values_mm
    profile = fetch_ssurgo_point(
        site.latitude_deg, site.longitude_deg,
        cache_path=str(DATA / f"ssurgo_{state}_{county}.json"),
    )
    return site, weather, et0, texture_from_ssurgo(profile, reg.texture)


def _deficits(weather, et0, reg, soil, crop_id, doy, duration, policy=RAINFED):
    out = {}
    for year in np.unique(weather.year):
        idx = np.flatnonzero((weather.year == year) & (weather.doy == doy))
        if not len(idx) or int(idx[0]) + duration > len(weather):
            continue
        r = run_water_balance(
            reg.crop(crop_id), soil, weather, et0, planting_index=int(idx[0]),
            duration_days=duration, irrigation=policy,
            initial_root_depletion_frac=0.2, initial_surface_depletion_frac=0.6,
            residue_cover_fraction=0.4,
        )
        harvest = int(year) + 1 if crop_id == "wheat_winter" else int(year)
        out[harvest] = r.relative_transpiration_deficit
    return out


# --- the sites really are different ----------------------------------------------


@pytest.mark.parametrize("key", list(SITES))
def test_each_site_loads_with_its_own_soil_and_weather(reg, key):
    site, weather, et0, soil = _load(reg, key)
    assert len(weather) > 14000
    assert np.all(np.isfinite(et0))
    assert soil.taw_is_measured, "SSURGO must supply the soil, not a centroid"
    expected = SITES[key][3]
    assert soil.taw_mm_per_m == pytest.approx(expected, rel=0.08)


def test_the_sites_span_a_real_range_of_soil_and_climate(reg):
    waters, rains = [], []
    for key in SITES:
        _, weather, _, soil = _load(reg, key)
        waters.append(soil.taw_mm_per_m)
        rains.append(
            np.mean([weather.precip_mm[weather.year == y].sum()
                     for y in np.unique(weather.year)[1:-1]])
        )
    assert max(waters) / min(waters) > 2.0, "soils must genuinely differ"
    assert max(rains) - min(rains) > 250.0, "climates must genuinely differ"


def test_the_sandhills_site_holds_far_less_water_than_the_pilot(reg):
    """The hardest test of transferability: a coarse soil, not a silt loam."""
    _, _, _, sand = _load(reg, "holt")
    _, _, _, pilot = _load(reg, "finney")
    assert sand.taw_mm_per_m < pilot.taw_mm_per_m / 2.0
    assert sand.is_coarse


# --- skill transfers --------------------------------------------------------------


DRYLAND_CASES = [
    ("finney", "maize", "CORN", 125, 150, 1.25),
    ("sedgwick", "maize", "CORN", 125, 150, 1.25),
    ("holt", "maize", "CORN", 125, 150, 1.25),
    ("finney", "wheat_winter", "WHEAT", 274, 285, 1.05),
    ("hale", "wheat_winter", "WHEAT", 274, 285, 1.05),
    ("holt", "wheat_winter", "WHEAT", 274, 285, 1.05),
]


@pytest.mark.parametrize("key,crop_id,commodity,doy,duration,ky", DRYLAND_CASES)
def test_dryland_skill_is_positive_at_every_site(
    reg, key, crop_id, commodity, doy, duration, ky
):
    """The sign is the claim. The model must never anti-correlate with yield."""
    cache = DATA / f"quickstats_{SITES[key][1]}_{SITES[key][2]}_{commodity}_NON-IRRIGATED.json".lower()
    if not cache.exists():
        pytest.skip(f"no cached yields for {key}/{commodity}")
    observed = parse_yield_rows(json.loads(cache.read_text()), commodity, NON_IRRIGATED)
    _, weather, et0, soil = _load(reg, key)
    modelled = _deficits(weather, et0, reg, soil, crop_id, doy, duration)
    years = np.array([y for y in observed.years if int(y) in modelled])
    if len(years) < 8:
        pytest.skip("too few paired years")
    residual = observed.detrend()[np.isin(observed.years, years)]
    predicted = -np.clip(
        ky * np.array([modelled[int(y)] for y in years]), 0.0, 1.0
    )
    s = skill(predicted, residual)
    assert s["r"] > 0.0, f"{key}/{commodity}: r={s['r']:+.3f}"


def test_most_dryland_combinations_are_statistically_significant(reg):
    """8 of 11 reach p<0.05 in the full run; require a clear majority here."""
    results = []
    for key, crop_id, commodity, doy, duration, ky in DRYLAND_CASES:
        cache = (
            DATA
            / f"quickstats_{SITES[key][1]}_{SITES[key][2]}_{commodity}_NON-IRRIGATED.json".lower()
        )
        if not cache.exists():
            continue
        observed = parse_yield_rows(
            json.loads(cache.read_text()), commodity, NON_IRRIGATED
        )
        _, weather, et0, soil = _load(reg, key)
        modelled = _deficits(weather, et0, reg, soil, crop_id, doy, duration)
        years = np.array([y for y in observed.years if int(y) in modelled])
        if len(years) < 8:
            continue
        residual = observed.detrend()[np.isin(observed.years, years)]
        predicted = -np.clip(
            ky * np.array([modelled[int(y)] for y in years]), 0.0, 1.0
        )
        results.append(skill(predicted, residual))
    assert len(results) >= 4
    assert all(r["r"] > 0.0 for r in results)
    assert sum(1 for r in results if r["p_value"] < 0.05) >= len(results) * 0.6
    assert np.mean([r["r"] for r in results]) > 0.3
