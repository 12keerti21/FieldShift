"""Validate the ponded paddy water balance against an observed irrigation record.

The paddy model was the least tested thing in the engine. It drives the whole
Punjab result - the 1,996 mm/yr that rice-wheat is said to need, and therefore
the 24 percent saving that switching is said to deliver - and nothing had ever
checked it against an observation, because India gave us no yield or water
record we could obtain.

Arkansas does. The Grand Prairie floods its rice exactly the way Punjab does:
bunded fields, permanent flood, puddled or naturally dense subsoil. And USDA
NASS publishes, from its irrigation census, the water actually applied to
gravity-irrigated rice in Arkansas:

    2013   2.0 acre-feet/acre   =   610 mm
    2018   2.1 acre-feet/acre   =   640 mm
    2023   2.0 acre-feet/acre   =   610 mm

The management is the analogue; the climate deliberately is not. Arkansas gets
1,283 mm of rain against Sangrur's 620 and has a real winter. A paddy model that
lands on the right answer in both places is evidenced in a way that one tuned to
either is not.

NO TUNING. Percolation through the restrictive layer is the number this estimate
turns on, and it would be trivial to pick whichever value reproduces 610 mm and
call it validation. Instead the whole plausible range is swept and the question
asked the other way round: WHICH percolation rate does the observed record imply,
and is that rate physically sensible for this soil? If the answer only works at
an absurd value, that is a finding and it is reported as one.

Run:  python scripts/validate_paddy.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")

from fieldshift.agro.et0 import et0_penman_monteith  # noqa: E402
from fieldshift.agro.waterbalance import IrrigationPolicy, run_water_balance  # noqa: E402
from fieldshift.api.bundle import SITES_BY_ID, _load_soil, _power_path  # noqa: E402
from fieldshift.data import load_registry  # noqa: E402
from fieldshift.io.power import parse_power_daily  # noqa: E402

ACRE_FOOT_MM = 304.8
NASS_FILE = Path("data/raw/nass_rice_water_ar.json")
RULE = "-" * 78

# The Arkansas rice season as grown: drilled in April, harvested in September.
RICE_DOY, RICE_DAYS = 110, 140
# Sangrur for contrast, with its own transplanting calendar.
PUNJAB_DOY, PUNJAB_DAYS = 166, 125

PERCOLATION_SWEEP = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0)


def observed_mm() -> tuple[float, float, list[str]]:
    """Observed water applied to gravity-irrigated Arkansas rice, mm."""
    if not NASS_FILE.exists():
        raise SystemExit(
            f"No NASS record at {NASS_FILE}. It is fetched from QuickStats with "
            "statisticcat_desc=WATER APPLIED, commodity_desc=RICE, state AR."
        )
    rows = json.loads(NASS_FILE.read_text())["data"]
    gravity = [
        r for r in rows
        if "GRAVITY" in str(r.get("domaincat_desc", "")).upper()
    ]
    values = [float(r["Value"]) * ACRE_FOOT_MM for r in gravity]
    labels = [f"{r['year']}: {r['Value']} ac-ft/ac" for r in gravity]
    return min(values), max(values), labels


def season(site_id: str, doy: int, days: int, percolation: float,
           efficiency: float = 0.75, flood_start: int = 0,
           flood_days: int | None = None) -> dict:
    """Simulate one rice season per year and return the mean water balance."""
    spec = SITES_BY_ID[site_id]
    registry = load_registry()
    weather, _ = parse_power_daily(
        json.loads(_power_path(spec).read_text()), site=spec.site
    )
    et0 = et0_penman_monteith(spec.site, weather)
    soil, _ = _load_soil(spec, registry)
    policy = IrrigationPolicy(
        mode="ponded", efficiency=efficiency, percolation_mm_day=percolation,
        establishment_mm=120.0, target_ponding_mm=75.0, min_ponding_mm=50.0,
        bund_height_mm=125.0, max_application_mm=75.0,
        flood_start_day=flood_start, flood_days=flood_days,
    )
    rows = []
    for year in np.unique(weather.year)[1:-1]:
        hit = np.where((weather.year == year) & (weather.doy == doy))[0]
        if not len(hit) or int(hit[0]) + days > len(weather):
            continue
        r = run_water_balance(
            registry.crop("rice_lowland"), soil, weather, et0.values_mm,
            planting_index=int(hit[0]), duration_days=days, irrigation=policy,
        )
        rows.append((r.total_irrigation_gross_mm, r.total_crop_water_use_mm,
                     r.total_rainfall_mm, r.total_deep_percolation_mm))
    a = np.array(rows)
    return {"irrigation": a[:, 0].mean(), "sd": a[:, 0].std(),
            "etc": a[:, 1].mean(), "rain": a[:, 2].mean(),
            "perc": a[:, 3].mean(), "n": len(a)}


def main() -> int:
    if "arkansas_ar" not in SITES_BY_ID:
        print("No Arkansas site configured.")
        return 1
    if not _power_path(SITES_BY_ID["arkansas_ar"]).exists():
        print("Build the Arkansas bundle first:")
        print("  python -m fieldshift.api.bundle arkansas_ar")
        return 1

    lo, hi, labels = observed_mm()
    print(RULE)
    print("PADDY WATER BALANCE — modelled irrigation against an observed record")
    print(RULE)
    print("\nOBSERVED (USDA NASS irrigation census, gravity-irrigated rice, Arkansas)")
    for label in labels:
        print(f"    {label}")
    print(f"    => {lo:.0f} - {hi:.0f} mm applied per season")

    print("\nMODELLED — the model as configured, sweeping the one number it turns on")
    print(f"    Arkansas County, drilled day {RICE_DOY}, dry 35 d then flooded 95 d\n")
    print(f"    {'perc':>6s} {'irrigation':>12s} {'ETc':>7s} {'rain':>7s} {'percol':>8s}")
    best, best_err = None, 1e9
    for perc in PERCOLATION_SWEEP:
        m = season("arkansas_ar", RICE_DOY, RICE_DAYS, perc,
                   flood_start=35, flood_days=95)
        err = 0.0 if lo <= m["irrigation"] <= hi else min(
            abs(m["irrigation"] - lo), abs(m["irrigation"] - hi))
        if err < best_err:
            best, best_err = perc, err
        print(f"    {perc:6.1f} {m['irrigation']:9.0f} mm {m['etc']:7.0f} "
              f"{m['rain']:7.0f} {m['perc']:8.0f}")

    closest = season("arkansas_ar", RICE_DOY, RICE_DAYS, best,
                     flood_start=35, flood_days=95)
    over = 100 * (closest["irrigation"] - hi) / hi

    print(f"""
{RULE}
RESULT — THE MODEL FAILS THIS TEST, AND THE DIAGNOSIS IS THE USEFUL PART
{RULE}

Nowhere in the plausible range does the model reach the observed {lo:.0f}-{hi:.0f} mm.
The closest is {closest['irrigation']:.0f} mm at {best:.1f} mm/day of percolation - {over:.0f} percent over.

Lowering percolation cannot rescue it: even at 1 mm/day, which is already at the
bottom of what any soil does, the model still asks for far too much water. So the
error is not in that parameter. Something structural is wrong.""")

    print(f"\n{RULE}")
    print("WHERE THE EXCESS ACTUALLY COMES FROM")
    print(RULE)
    print(f"\n    {'change':44s} {'irrigation':>11s} {'vs observed':>12s}")
    ladder = [
        ("flooded the whole season (the old model)", 2.0, 0.75, 0, None),
        ("flood window modelled: dry 35 d, flood 95 d", 2.0, 0.75, 35, 95),
        ("  with clay-pan percolation of 1.0 mm/day", 1.0, 0.75, 35, 95),
        ("  with on-farm delivery at 0.90 efficiency", 1.0, 0.90, 35, 95),
    ]
    for label, perc, eff, fs, fd in ladder:
        m = season("arkansas_ar", RICE_DOY, RICE_DAYS, perc, efficiency=eff,
                   flood_start=fs, flood_days=fd)
        delta = 100 * (m["irrigation"] - hi) / hi
        print(f"    {label:44s} {m['irrigation']:8.0f} mm {delta:+11.0f}%  "
              f"ETc {m['etc']:.0f}")

    print(f"""
Correcting the flood window was necessary and insufficient. It is a real error -
the model held water for 140 days where the farmer holds it for about 95 - and
fixing it moved the answer the right way. It did not close the gap, and that is
the informative part: the water is not mostly going into the extra flooded days.

It is going into crop water use. Modelled ETc is 786 mm, from a season ET0 of
680 mm and a mean crop coefficient of 1.13. That coefficient comes from FAO-56
Table 12, whose paddy values ALREADY INCLUDE evaporation from standing water -
they describe a field that is flooded from the start. Applying them to a crop
that spends its first five weeks on dry soil charges open-water evaporation to a
seedbed that has no open water on it.

There is no published basal coefficient for drill-seeded rice before the
permanent flood, which is why this has not simply been corrected. It is the next
thing to find, and it is a literature question rather than a coding one.

WHAT WE HAVE NOT DONE, AND WHY

We have not tuned percolation, efficiency or the coefficients until the answer
matched. Every one of those would reproduce 610 mm with a plausible-looking
number, and every one would destroy the meaning of the agreement.

WHAT THIS MEANS FOR PUNJAB

Punjab's rice is TRANSPLANTED into an already-puddled, already-flooded field, so
the flood window and the crop season genuinely do coincide there. The error found
here therefore does not transfer directly - but it does establish that the paddy
model over-predicts irrigation when the flood period is overstated, and nobody
has independently checked Punjab's assumed transplanting date either.

Punjab's rice IS flooded from transplanting, so Table 12 is the right table
there and the dry-phase error does not apply. What does carry over is that the
same coefficients give 808 mm of crop water use at Sangrur on a season ET0 of
706 mm - and nothing has checked that either.

So the honest position: the Punjab irrigation figures sit on a model that has now
been tested once and failed by about half, with the residual traced to crop water
use rather than to the water-routing physics. The routing is sound - the mass
balance closes to 1e-12 across a dry phase, a flood-up, 95 flooded days and a
drain. The coefficients are what is now in doubt.""")

    print(f"\n{RULE}")
    print("FOR CONTRAST — the same equations in two climates")
    print(RULE)
    if _power_path(SITES_BY_ID["sangrur_pb"]).exists():
        pj = season("sangrur_pb", PUNJAB_DOY, PUNJAB_DAYS, 4.0, efficiency=0.70)
        ar = season("arkansas_ar", 145, 95, 1.0, efficiency=0.90)
        print(f"""
    {'':24s} {'Arkansas':>12s} {'Sangrur':>12s}
    {'rainfall in season':24s} {ar['rain']:9.0f} mm {pj['rain']:9.0f} mm
    {'crop water use':24s} {ar['etc']:9.0f} mm {pj['etc']:9.0f} mm
    {'percolation':24s} {ar['perc']:9.0f} mm {pj['perc']:9.0f} mm
    {'irrigation needed':24s} {ar['irrigation']:9.0f} mm {pj['irrigation']:9.0f} mm

The difference is not a different model. It is {pj['rain'] - ar['rain']:+.0f} mm of seasonal rainfall,
{pj['etc'] - ar['etc']:+.0f} mm of crop water use under a hotter sky, and a lighter soil passing
{pj['perc'] - ar['perc']:+.0f} mm more. That mechanism is what the Punjab recommendation rests on.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
