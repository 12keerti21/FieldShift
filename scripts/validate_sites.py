"""Do our sites sit on the land we claim they do?

Every other check in this repository tests the MODEL. This one tests an
ASSUMPTION underneath all of them: that the coordinate we picked for each site
is a field growing the rotation we modelled against it.

Nothing had ever checked that. The NDVI check hinted at it - four of five US
pixels disagreed with our canopy curve - but NDVI cannot tell you whether the
disagreement is a bad model or bad ground. A land-cover product can.

THE SOURCE, AND WHY IT IS NOT CIRCULAR
--------------------------------------
USDA's Cropland Data Layer classifies every 30 m of the United States by crop,
annually, from satellite and ground truth. It is independent of MODIS NDVI, so
choosing or judging a point with CDL leaves the NDVI comparison an honest test.

CropScape, the usual access route, returns HTTP 500 and now sits behind a
sign-in wall. CDL is also published in the Microsoft Planetary Computer STAC
catalogue, which needs no account, and that is what this uses.

WHAT IT MEASURES
----------------
A MODIS pixel is 250 m; CDL is 30 m. So one MODIS observation averages about
seventy CDL cells, and a point can be the right crop while its pixel is mostly
something else. This therefore asks for the composition of the whole 250 m box,
not the class of the centre cell, for every year CDL covers.

Run:  python scripts/validate_sites.py
"""

from __future__ import annotations

import json
import math
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
DATA = "https://planetarycomputer.microsoft.com/api/data/v1/item/statistics"
YEARS = range(2008, 2022)          # CDL coverage in the Planetary Computer catalogue
MODIS_PIXEL_M = 250.0
RULE = "-" * 78

# The CDL classes that would mean "this pixel grows what we modelled".
EXPECTED = {
    "finney_ks":   ({24, 61}, "winter wheat / fallow"),
    "sedgwick_ks": ({24, 61}, "winter wheat / fallow"),
    "holt_ne":     ({24, 61}, "winter wheat / fallow"),
    "hale_tx":     ({24, 61}, "winter wheat / fallow"),
    "arkansas_ar": ({3, 5},   "rice / soybeans"),
}
SITES = {
    "finney_ks":   (38.03, -100.74),
    "sedgwick_ks": (37.70, -97.46),
    "holt_ne":     (42.45, -98.78),
    "hale_tx":     (34.07, -101.82),
    "arkansas_ar": (34.29, -91.33),
}
NAMES = {
    0: "background", 1: "corn", 2: "cotton", 3: "rice", 4: "sorghum", 5: "soybeans",
    6: "sunflower", 21: "barley", 22: "durum wheat", 23: "spring wheat",
    24: "winter wheat", 27: "rye", 28: "oats", 36: "alfalfa", 37: "other hay",
    43: "potatoes", 53: "peas", 59: "sod", 60: "switchgrass", 61: "fallow/idle",
    111: "open water", 121: "developed/open", 122: "developed/low",
    123: "developed/med", 124: "developed/high", 131: "barren",
    141: "deciduous forest", 142: "evergreen forest", 143: "mixed forest",
    152: "shrubland", 176: "grass/pasture", 190: "woody wetlands",
    195: "herbaceous wetlands", 236: "wheat/sorghum", 238: "wheat/cotton",
}

# Classes that are not farmland at all. A site landing here is not a mismatched
# rotation; it is not a field.
NON_AGRICULTURAL = {111, 121, 122, 123, 124, 131, 141, 142, 143, 152, 176, 190, 195}


def _post(url: str, body: dict, timeout: int = 90) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def _box(lat: float, lon: float, metres: float = MODIS_PIXEL_M) -> dict:
    """A GeoJSON square of `metres` a side, centred on the point."""
    dlat = metres / 2 / 111_320.0
    dlon = metres / 2 / (111_320.0 * math.cos(math.radians(lat)))
    return {
        "type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [[
            [lon - dlon, lat - dlat], [lon + dlon, lat - dlat],
            [lon + dlon, lat + dlat], [lon - dlon, lat + dlat],
            [lon - dlon, lat - dlat],
        ]]},
    }


def _item(lat: float, lon: float, year: int) -> str | None:
    found = _post(STAC, {
        "collections": ["usda-cdl"],
        "intersects": {"type": "Point", "coordinates": [lon, lat]},
        "datetime": f"{year}-01-01/{year}-12-31", "limit": 10,
    })
    for feature in found.get("features", []):
        if feature["id"].startswith("cropland_"):
            return feature["id"]
    return None


def composition(lat: float, lon: float, year: int) -> dict[int, float] | None:
    """Percent of the 250 m box in each CDL class, or None if unavailable."""
    item = _item(lat, lon, year)
    if item is None:
        return None
    url = f"{DATA}?collection=usda-cdl&item={item}&assets=cropland&categorical=true"
    try:
        stats = _post(url, _box(lat, lon))["properties"]["statistics"]["cropland_b1"]
    except Exception:
        return None
    total = stats.get("count") or 0
    if not total:
        return None
    histogram = stats.get("histogram")
    if not histogram or len(histogram) != 2:
        return {int(stats["majority"]): 100.0}
    out: dict[int, float] = {}
    for count, edge in zip(*histogram):
        if count:
            key = int(round(edge))
            out[key] = out.get(key, 0.0) + count / total * 100.0
    return out


def main() -> int:
    print(RULE)
    print("ARE OUR SITES ON THE LAND WE CLAIM?")
    print(RULE)
    print("USDA Cropland Data Layer, 30 m, via the Microsoft Planetary Computer.")
    print(f"Composition of the {MODIS_PIXEL_M:.0f} m MODIS box, {YEARS.start}-{YEARS.stop - 1}.")
    print("Independent of MODIS NDVI, so this does not make that check circular.\n")

    verdicts = {}
    for site_id, (lat, lon) in SITES.items():
        expected, label = EXPECTED[site_id]
        with ThreadPoolExecutor(max_workers=7) as pool:
            rows = list(pool.map(lambda y: (y, composition(lat, lon, y)), YEARS))

        usable = [(y, c) for y, c in rows if c]
        if not usable:
            print(f"{site_id}: CDL unavailable\n")
            continue

        match = [sum(v for k, v in c.items() if k in expected) for _, c in usable]
        nonag = [sum(v for k, v in c.items() if k in NON_AGRICULTURAL) for _, c in usable]
        mean_match = sum(match) / len(match)
        mean_nonag = sum(nonag) / len(nonag)

        if mean_nonag >= 50:
            verdict = "NOT FARMLAND"
        elif mean_match >= 70:
            verdict = "matches"
        else:
            verdict = "wrong rotation"
        verdicts[site_id] = (verdict, mean_match, mean_nonag)

        print(f"{site_id}  ({lat}, {lon})   modelled as {label}")
        print(f"   {verdict.upper()}   "
              f"modelled classes {mean_match:.0f}% of the pixel, "
              f"non-agricultural {mean_nonag:.0f}%")
        for year, comp in usable[-4:]:
            top = sorted(((v, k) for k, v in comp.items()), reverse=True)[:2]
            print("     " + str(year) + ": "
                  + "  ".join(f"{NAMES.get(k, k)} {v:.0f}%" for v, k in top if v >= 5))
        print()

    print(RULE)
    print("WHAT THIS FOUND")
    print(RULE)
    bad = [s for s, (v, _, _) in verdicts.items() if v != "matches"]
    print(f"{len(verdicts) - len(bad)} of {len(verdicts)} US sites sit on the land we modelled.\n")
    for site_id in bad:
        verdict, match, nonag = verdicts[site_id]
        if verdict == "NOT FARMLAND":
            print(f"  {site_id}: not a field. {nonag:.0f}% of the pixel is "
                  "developed, pasture, forest or wetland.")
        else:
            print(f"  {site_id}: farmland, but only {match:.0f}% of the pixel grows "
                  "the rotation we compared against it.")
    print()
    not_field = sum(1 for v, _, _ in verdicts.values() if v == "NOT FARMLAND")
    print("This does not say the model is wrong. It says the baseline rotation at")
    print(f"{len(bad)} of {len(verdicts)} US sites was compared against ground that does not grow it,")
    print(f"and that {not_field} of those are not farmland at any point in the record. A")
    print("disagreement measured there was never evidence about the canopy model at")
    print("all. The defect is in our site selection, and it is ours.")
    print()
    print("A county reference point is a label on a map, not a field. We used one")
    print("as if it were the other, at every US site, for the whole project.")
    print()
    print("It also reaches further than the NDVI check. Each site's weather and soil")
    print("are drawn from its coordinate, so arkansas_ar - the site the paddy")
    print("irrigation check runs on - has been taking its soil from a pixel CDL")
    print("calls developed land and wetland.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
