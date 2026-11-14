"""Validate our FAO-56 Penman-Monteith ET0 against gridMET's published product.

gridMET distributes grass reference ET at 4 km as its own product, computed from a
different forcing dataset. That makes it an independent check on our ET0 rather
than an input to it - the strongest validation available for this module without
a flux tower.

    PYTHONPATH=src ./.venv/bin/python scripts/validate_et0.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from fieldshift.agro.climate import Site
from fieldshift.agro.et0 import et0_penman_monteith
from fieldshift.io.gridmet import dataset_scale_factor, parse_gridmet_csv
from fieldshift.io.power import parse_power_daily

SITE = Site(38.03, -100.74, 858.0, "Finney County, Kansas")
POWER_CACHE = Path("data/raw/power_finney_ks_1981_2024.json")


def ours() -> tuple[np.ndarray, np.ndarray]:
    payload = json.loads(POWER_CACHE.read_text())
    for series in payload["properties"]["parameter"].values():
        for k in list(series):
            if k < "19840101":
                del series[k]
    w, _ = parse_power_daily(payload, site=SITE)
    return w.dates, et0_penman_monteith(SITE, w).values_mm


def theirs() -> tuple[np.ndarray, np.ndarray]:
    scale, offset = dataset_scale_factor("eto")
    dates, values = [], []
    for path in sorted(Path("data/raw").glob("gridmet_eto_*.csv")):
        s = parse_gridmet_csv(path.read_text(encoding="utf-8"), "eto", scale, offset)
        dates.append(s.dates)
        values.append(s.values)
    d = np.concatenate(dates)
    v = np.concatenate(values)
    keep = np.unique(d, return_index=True)[1]
    return d[keep], v[keep]


def main() -> int:
    od, ov = ours()
    gd, gv = theirs()
    common, i_o, i_g = np.intersect1d(od, gd, return_indices=True)
    o, g = ov[i_o], gv[i_g]

    print("Validation: FAO-56 Penman-Monteith ET0 vs gridMET published ETo")
    print(f"Site {SITE.name}  |  {len(common)} overlapping days  "
          f"{common[0]} to {common[-1]}\n")

    years = common.astype("datetime64[Y]").astype(int) + 1970
    whole = [y for y in np.unique(years) if (years == y).sum() >= 364]
    ann_o = np.array([o[years == y].sum() for y in whole])
    ann_g = np.array([g[years == y].sum() for y in whole])
    month = common.astype("datetime64[M]").astype(int) % 12 + 1
    season = (month >= 5) & (month <= 9)

    bias = o.mean() / g.mean() - 1
    rows = [
        ("annual ETo, mm", ann_o.mean(), ann_g.mean()),
        ("May-Sep ETo, mm", o[season].sum() / len(whole), g[season].sum() / len(whole)),
        ("daily mean, mm", o.mean(), g.mean()),
        ("July daily mean, mm", o[month == 7].mean(), g[month == 7].mean()),
        ("January daily mean, mm", o[month == 1].mean(), g[month == 1].mean()),
    ]
    print(f"  {'quantity':26s} {'ours':>9s} {'gridMET':>9s} {'diff':>8s}")
    for label, a, b in rows:
        print(f"  {label:26s} {a:9.2f} {b:9.2f} {a/b-1:+7.1%}")

    print(f"\n  {'daily correlation':26s} {np.corrcoef(o, g)[0, 1]:9.3f}")
    print(f"  {'daily RMSE, mm':26s} {np.sqrt(((o - g) ** 2).mean()):9.2f}")
    print(f"  {'daily MAE, mm':26s} {np.abs(o - g).mean():9.2f}")
    print(f"  {'annual correlation':26s} {np.corrcoef(ann_o, ann_g)[0, 1]:9.3f}")
    print(f"  {'overall bias':26s} {bias:+9.1%}")

    verdict = "AGREES" if abs(bias) < 0.10 else "BIASED"
    print(f"\n  VERDICT: {verdict} - our ET0 differs from an independently computed")
    print(f"  4 km published product by {bias:+.1%} over {len(whole)} complete years.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
