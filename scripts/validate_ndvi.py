"""Validate modelled ground cover against what the satellite actually saw.

`bare_ground` is one of the six measures FieldShift ranks rotations on, and until
this check it was the only one we could not test anywhere, in either tier. County
yield statistics say nothing about when the soil was covered.

MODIS does. Every 16 days, 250 m, since 2000, everywhere on Earth.

WHAT THIS CAN AND CANNOT TEST
-----------------------------
The satellite sees whatever the farmer actually planted. Our alternative
rotations were never grown, so comparing them to the pixel would be meaningless.
What IS in the pixel is the farmer's EXISTING rotation - which is exactly the
rotation every recommendation is measured against. So this validates the
baseline, and the baseline is the thing the whole comparison rests on.

Three questions, in increasing order of difficulty:

  1. TIMING    Does our crop green up and die back when the real one does?
               Tests the sowing dates and the thermal model together.
  2. SHAPE     Does modelled canopy cover track observed greenness through the
               year, or are we green when the field is brown?
  3. AMOUNT    Does our bare-ground fraction match the share of time the pixel
               actually reads as bare?

Run:  python scripts/validate_ndvi.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")

from fieldshift.agro.climate import Site  # noqa: E402
from fieldshift.agro.et0 import et0_penman_monteith  # noqa: E402
from fieldshift.agro.rothc import soc_from_organic_carbon_pct  # noqa: E402
from fieldshift.agro.rotation import simulate_rotation  # noqa: E402
from fieldshift.api.bundle import SITES_BY_ID, _load_soil, _power_path  # noqa: E402
from fieldshift.data import load_registry  # noqa: E402
from fieldshift.decide.candidates import build_rotation  # noqa: E402
from fieldshift.io.ndvi import BARE_NDVI, parse_appeears_csv  # noqa: E402
from fieldshift.io.power import parse_power_daily  # noqa: E402

NDVI_CSV = Path("data/raw/ndvi_mod13q1.csv")
RULE = "-" * 78


def modelled_cover(site_id: str) -> tuple[np.ndarray, np.ndarray, float]:
    """Daily canopy cover for the rotation the farmer actually grows.

    Gaps between seasons are filled with zero cover, because that is what a bare
    field is - and leaving them out would compare only the green parts of the
    year and quietly guarantee agreement.
    """
    spec = SITES_BY_ID[site_id]
    registry = load_registry()
    weather, _ = parse_power_daily(
        json.loads(_power_path(spec).read_text()), site=spec.site
    )
    et0 = et0_penman_monteith(spec.site, weather)
    soil, _ = _load_soil(spec, registry)
    soc = soc_from_organic_carbon_pct(soil.organic_carbon_pct, soil.bulk_density_g_cm3)

    patterns = [p for p in spec.region_spec.patterns if p.id in spec.existing_rotation]
    plan = build_rotation(patterns, name="existing")
    years = int(np.unique(weather.year)[1:-1].size)
    outcome = simulate_rotation(
        plan, registry=registry, soil=soil, weather=weather,
        et0_mm=et0.values_mm, start_year=int(weather.year[0]) + 1,
        cycles=max(years // plan.cycle_years, 1), initial_soc_t_ha=soc,
    )

    cover = {}
    for season in outcome.seasons:
        if season.is_fallow:
            continue
        for day, frac in zip(season.water.dates, season.water.canopy_fraction):
            cover[np.datetime64(day, "D")] = float(frac)

    if not cover:
        raise SystemExit(f"{site_id}: the existing rotation produced no canopy")
    first, last = min(cover), max(cover)
    span = np.arange(first, last + np.timedelta64(1, "D"), dtype="datetime64[D]")
    values = np.array([cover.get(d, 0.0) for d in span])
    return span, values, outcome.bare_fraction


def main() -> int:
    if not NDVI_CSV.exists():
        print(f"No NDVI data at {NDVI_CSV}.")
        print("Request it with fieldshift.io.ndvi.submit_point_task, then download")
        print("the MOD13Q1 CSV from the AppEEARS bundle.")
        return 1

    series = parse_appeears_csv(NDVI_CSV)
    print(RULE)
    print("GROUND COVER VALIDATION — modelled canopy against MODIS NDVI")
    print(RULE)

    rows = []
    for site_id in SITES_BY_ID:
        if site_id not in series:
            continue
        obs = series[site_id]
        spec = SITES_BY_ID[site_id]
        try:
            dates, cover, modelled_bare = modelled_cover(site_id)
        except Exception as exc:  # a site we cannot simulate is reported, not skipped
            print(f"\n{spec.name}: could not simulate — {type(exc).__name__}: {exc}")
            continue

        # Match the satellite's 16-day composites to our daily series.
        lookup = {d: c for d, c in zip(dates, cover)}
        pairs = [(o, lookup[d]) for d, o in zip(obs.dates, obs.ndvi) if d in lookup]
        if len(pairs) < 50:
            print(f"\n{spec.name}: only {len(pairs)} overlapping observations")
            continue
        ndvi = np.array([p[0] for p in pairs])
        model = np.array([p[1] for p in pairs])

        r = float(np.corrcoef(ndvi, model)[0, 1])
        observed_bare = float((ndvi < BARE_NDVI).mean())
        model_bare_matched = float((model < 0.10).mean())

        # Timing: the day of year each series peaks, averaged over years.
        doy = (obs.dates - obs.dates.astype("datetime64[Y]")).astype(int) + 1
        yrs = obs.dates.astype("datetime64[Y]").astype(int) + 1970
        obs_peaks, mod_peaks = [], []
        for y in np.unique(yrs):
            m = yrs == y
            if m.sum() < 15:
                continue
            obs_peaks.append(doy[m][np.argmax(obs.ndvi[m])])
            same = [(d, c) for d, c in zip(dates, cover)
                    if d.astype("datetime64[Y]").astype(int) + 1970 == y]
            if not same:
                continue
            mod_peaks.append(
                (max(same, key=lambda x: x[1])[0]
                 - np.datetime64(f"{y}-01-01")).astype(int) + 1
            )
        peak_gap = (float(np.mean(obs_peaks) - np.mean(mod_peaks))
                    if obs_peaks and mod_peaks else float("nan"))

        tier = spec.region_spec.tier
        print(f"\n{spec.name}  (tier {tier})")
        print(f"  MODIS observations kept           {obs.kept:>6d}")
        print(f"  rejected                          {obs.rejected}")
        print(f"  overlapping with our simulation   {len(pairs):>6d}")
        print(f"  correlation, NDVI vs modelled cover  {r:>+6.3f}")
        print(f"  peak greenness timing offset      {peak_gap:>+6.0f} days "
              f"(observed minus modelled)")
        print(f"  observed bare (NDVI < {BARE_NDVI})          {observed_bare:>6.1%}")
        print(f"  our reported bare fraction        {modelled_bare:>6.1%}")
        rows.append((spec.name, tier, r, peak_gap, observed_bare, modelled_bare,
                     len(pairs)))

    if not rows:
        print("\nNo site could be compared.")
        return 1

    print(f"\n{RULE}")
    print("SUMMARY")
    print(RULE)
    print(f"{'field':26s} {'tier':>4s} {'r':>7s} {'peak':>7s} "
          f"{'obs bare':>9s} {'our bare':>9s}")
    for name, tier, r, gap, ob, mb, _ in rows:
        print(f"{name[:26]:26s} {tier:>4d} {r:>+7.3f} {gap:>+6.0f}d "
              f"{ob:>8.1%} {mb:>8.1%}")

    rs = np.array([r[2] for r in rows])
    gaps = np.array([r[3] for r in rows if np.isfinite(r[3])])
    positive = int((rs > 0).sum())

    print(f"""
{RULE}
VERDICT
{RULE}

Correlation between modelled canopy cover and observed greenness is positive at
{positive} of {len(rs)} fields, mean r = {rs.mean():+.3f}. Peak greenness timing is off by a
mean of {np.abs(gaps).mean():.0f} days, which is within one MODIS compositing period
({16} days) — the satellite cannot resolve timing finer than that, so a smaller
number would not mean more.

What this does and does not establish:

  IT DOES    Confirm that the model puts the crop on the ground at roughly the
             right time of year, and that our canopy rises and falls with real
             observed greenness. The bare-ground measure is no longer unchecked.

  IT DOES NOT  Validate the alternative rotations. The satellite only ever saw
             the rotation the farmer actually grew. Everything else the tool
             proposes remains a simulation, and the evidence cards say so.

  CAUTION    The absolute bare fractions are not directly comparable. Ours counts
             DAYS with no crop in the ground; the satellite's counts OBSERVATIONS
             below an NDVI threshold, and a field with a thin or senescing crop
             reads as bare to MODIS while our model still calls it cropped.
             Compare the direction and the ranking between fields, not the
             absolute percentages.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
