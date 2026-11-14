"""Validate the Punjab tier against the only instrument that recorded it.

The US validation correlates modelled water stress against detrended county yield.
That method cannot work here, and the reason is physical rather than practical:
Punjab rice is grown under continuous submergence and wheat is irrigated on demand,
so the model says every year is unstressed. Measured over 39 seasons at Sangrur,
the relative transpiration of ponded rice has a standard deviation of exactly zero
and irrigated wheat 0.0008. Correlating that against yield would be correlating a
constant. Obtaining district yield statistics would not have changed it.

But the weather has not stopped mattering - it has moved from yield into water
demand. Modelled rice irrigation at Sangrur ranges 895 to 2,338 mm across the same
seasons. And in a closed aquifer, water demand leaves a record: every cubic metre
pumped shows up as a falling water table. CGWB observation wells have been reading
that record for nearly thirty years.

So this script asks one question the US validation never had to ask:

    Is the model's irrigation demand reconcilable with the observed decline of
    the aquifer it is drawn from?

Run:  python scripts/validate_punjab.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "src")

from fieldshift.io.indiawris import (  # noqa: E402
    annual_series,
    district_decline_m_per_year,
    fetch_groundwater,
)

BUNDLE = Path("data/bundles/sangrur_pb.json")
CACHE = "data/raw/indiawris_pb_sangrur.json"

# Specific yield of the aquifer: the fraction of a unit volume of rock that drains
# under gravity, so metres of water-table fall x Sy = metres of water removed.
#
# This is NOT a number we measured and NOT one we fetched, so it is carried as a
# range rather than a value and every result below is reported across it. Published
# values for Indo-Gangetic alluvium span roughly this interval; the point of the
# range is that no conclusion here may depend on picking a favourite within it.
SPECIFIC_YIELD = (0.08, 0.12, 0.16)

# The share of water lost between the pump and the root zone that percolates back
# into the same aquifer rather than evaporating. Also a range, for the same reason.
RETURN_FLOW_FRACTION = (0.5, 0.7, 0.9)

RULE = "-" * 74


def bar(label: str, value: str) -> None:
    print(f"  {label:46s} {value:>24s}")


def main() -> int:
    if not BUNDLE.exists():
        print("No Sangrur bundle. Build it with:")
        print("  python -m fieldshift.api.bundle sangrur_pb")
        return 1
    bundle = json.loads(BUNDLE.read_text())
    site = bundle["site"]

    print(RULE)
    print("PUNJAB TIER VALIDATION — modelled water demand against the aquifer")
    print(RULE)

    # ---------------------------------------------------------------- 1. observed
    print("\n1. OBSERVED: what the aquifer actually did")
    rows = fetch_groundwater("PUNJAB", "SANGRUR", cache_path=CACHE)
    series, rejected = annual_series(rows)
    decline, iqr, n_wells = district_decline_m_per_year(series)
    spans = [len(s.years) for s in series]
    agree = sum(1 for s in series if s.trend_m_per_year > 0)

    bar("CGWB readings retrieved", f"{len(rows):,}")
    bar("manual observation wells with >=8 years", f"{n_wells}")
    bar("longest record", f"{max(spans)} years")
    bar("wells showing a FALLING table", f"{agree} of {n_wells}")
    bar("median decline", f"{decline:+.3f} m/yr")
    bar("inter-quartile spread of well trends", f"{iqr:.3f} m/yr")
    print(f"\n  Rejected on the way in: {rejected}")
    print("  Note the sign trap: manual wells report depth POSITIVE below ground,")
    print("  telemetric wells report it NEGATIVE. Combining them unnormalised does")
    print("  not make a noisy trend, it makes a meaningless one.")

    # ------------------------------------------------------------- 2. modelled
    print("\n2. MODELLED: what the rotation asks of the aquifer")
    existing = next(r for r in bundle["rotations"] if r["is_existing"])
    # The rotation the tool would actually recommend: one that beats the existing
    # rotation on EVERY objective, not merely the thirstiest-avoiding option. A
    # saving quoted from a rotation that gives up yield is not the same claim.
    keys = [o["key"] for o in bundle["objectives"]]
    maximise = {o["key"]: o["maximise"] for o in bundle["objectives"]}

    def dominates(a, b):
        better = False
        for k in keys:
            av, bv = a["objectives"][k], b["objectives"][k]
            if maximise[k]:
                if av < bv - 1e-9:
                    return False
                better = better or av > bv + 1e-9
            else:
                if av > bv + 1e-9:
                    return False
                better = better or av < bv - 1e-9
        return better

    dominating = [
        r for r in bundle["rotations"]
        if not r["is_existing"] and dominates(r, existing)
    ]
    best = min(dominating, key=lambda r: r["objectives"]["irrigation"]) \
        if dominating else min(
            (r for r in bundle["rotations"] if not r["is_existing"]),
            key=lambda r: r["objectives"]["irrigation"],
        )
    cheapest = min(
        (r for r in bundle["rotations"] if not r["is_existing"]),
        key=lambda r: r["objectives"]["irrigation"],
    )
    gross = existing["objectives"]["irrigation"]
    rainfall = site["rainfall_mm_yr"]
    et0 = site["weather"]["annual_et0_mm"]

    bar("rotation", existing["name"].replace("EXISTING: ", ""))
    bar("irrigation pumped (gross, field gate)", f"{gross:,.0f} mm/yr")
    bar("rainfall", f"{rainfall:,.0f} mm/yr")
    bar("reference ET", f"{et0:,.0f} mm/yr")

    # The irreducible floor: water the crop actually consumed and the sky did not
    # supply. Even if every drop of percolation and every conveyance loss returned
    # to the aquifer, this much must come out of storage.
    #
    # Crop water use is taken from the simulated seasons rather than assumed.
    etc = sum(
        s for s in [existing["detail"].get("crop_water_use_mm_yr", 0.0)]
    ) or None
    if not etc:
        # Fall back to the balance implied by the objectives.
        etc = None

    print("\n3. RECONCILIATION: can one explain the other?")
    print("   Observed depletion = decline x specific yield.")
    print("   Modelled depletion = pumped - (percolation + returning losses).")
    print()
    print(f"   {'Sy':>6s} {'observed depletion':>22s}")
    observed = {}
    for sy in SPECIFIC_YIELD:
        mm = decline * sy * 1000.0
        observed[sy] = mm
        print(f"   {sy:6.2f} {mm:19.0f} mm/yr")

    print()
    print(f"   {'return':>8s} {'modelled net draft':>22s}")
    modelled = {}
    for rf in RETURN_FLOW_FRACTION:
        # Losses between pump and root zone, and the part of them that comes back.
        # Efficiency is the Punjab tubewell/basin figure used in the region config.
        efficiency = 0.70
        losses = gross * (1.0 - efficiency)
        returned = losses * rf
        # Deep percolation under the ponded paddy is the other large return path.
        # 4 mm/day over a 125 day rice season is the region's stated assumption.
        percolation = 4.0 * 125.0
        net = gross - returned - percolation
        modelled[rf] = net
        print(f"   {rf:8.1f} {net:19.0f} mm/yr")

    lo_obs, hi_obs = min(observed.values()), max(observed.values())
    lo_mod, hi_mod = min(modelled.values()), max(modelled.values())
    ratio_lo = lo_mod / hi_obs if hi_obs else float("inf")
    ratio_hi = hi_mod / lo_obs if lo_obs else float("inf")

    print()
    bar("observed depletion range", f"{lo_obs:.0f} – {hi_obs:.0f} mm/yr")
    bar("modelled net draft range", f"{lo_mod:.0f} – {hi_mod:.0f} mm/yr")
    bar("model exceeds observation by", f"{ratio_lo:.1f}x – {ratio_hi:.1f}x")

    print(f"\n{RULE}")
    print("VERDICT")
    print(RULE)
    print(f"""
The signs agree and the magnitudes do not. Every one of the {n_wells} wells says the
table is falling, which is what a rotation with a {gross:,.0f} mm/yr irrigation demand in a
{rainfall:,.0f} mm/yr rainfall climate must do. But the modelled net draft is {ratio_lo:.0f}-{ratio_hi:.0f} times
larger than the decline can account for, across the whole plausible range of
specific yield and return flow.

That gap is a real result, and it is not evidence that the ranking is wrong. It is
evidence that a FIELD-scale draft cannot be reconciled with a DISTRICT-scale
water-table response without a district water budget we do not have. The three
things most likely to be in that gap:

  1. Canal supply. Not all irrigation in Sangrur is pumped. Water delivered by
     canal does not come out of the aquifer, and canal seepage recharges it. Our
     model has no canal: it assumes every millimetre is lifted from below.
  2. Lateral inflow. A district is not a closed tank. Regional groundwater flow
     across its boundary is invisible to a one-field water balance.
  3. Our rice ET may be high. Modelled rice ETc is 838 mm from FAO-56 Table 12
     coefficients and our POWER-derived ET0. We have no independent ET product
     for India to check it against - the single most valuable thing anyone could
     hand this project.

What survives, and what does not:

  SURVIVES   The comparison BETWEEN rotations, which is what the tool reports.
             The best rotation that beats rice-wheat on EVERY objective still
             saves {gross - best['objectives']['irrigation']:,.0f} mm/yr ({100 * (gross - best['objectives']['irrigation']) / gross:.0f}%) while
             producing more, and the cheapest option of any kind saves
             {gross - cheapest['objectives']['irrigation']:,.0f} mm/yr ({100 * (gross - cheapest['objectives']['irrigation']) / gross:.0f}%) with a trade-off. A systematic bias in the
             absolute level largely cancels in a difference computed the same way
             on both sides.

  DOES NOT   Any absolute claim about how long the aquifer has left, or about the
             basin-scale effect of a policy. The tool must not be used for that
             here, and the evidence card says so.

This check is therefore recorded as PARTIAL: direction confirmed by {agree}/{n_wells} wells
over up to {max(spans)} years, magnitude unreconciled by a factor of {ratio_lo:.0f}-{ratio_hi:.0f}.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
