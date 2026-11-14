"""SoilGrids adapter via ISRIC REST: global soil, no credentials.

This is the soil source for Tier 2 - anywhere SSURGO does not reach. It matters
because soil is the one input with no global equivalent of NASA POWER: weather is
genuinely worldwide, soil surveys are not. Without a global soil source the engine
simply cannot leave the United States, so this adapter is what makes the
transferability claim true rather than aspirational.

It is deliberately NOT presented as equivalent to SSURGO, and the difference is not
cosmetic:

    SSURGO      a field survey. Available water capacity is a measured
                representative value for a mapped soil component, and NRCS assigns
                the hydrologic group from the limiting layer.
    SoilGrids   a 250 m global machine-learning prediction from ~240k profiles.
                Water retention is itself predicted, there is no hydrologic group
                at all, and no polygon boundary - the 250 m cell straddles whatever
                is nearby.

Two findings from using it, both recorded because they change how the output should
be read:

1. The service answers HTTP 200 with every value null when it is rate-limiting,
   rather than failing or returning 429. This is throttling wearing the costume of
   missing data, and an adapter that trusted the first 200 would silently report
   "no soil here" for most of the world.

   The cooldown is longer than a short backoff can ride out. Measured here: ten
   points in quick succession returned data for two; isolated requests minutes
   later succeeded for those same two; and after a burst of seven, four further
   requests spaced 25 seconds apart over 75 seconds all still came back null -
   including for a point that had answered moments earlier. So the retry below
   helps with a brief squeeze and cannot help with an exhausted quota.

   Two consequences, both deliberate. An all-null body is a failure to FETCH and
   never an absence of soil. And because a cooldown can outlast any reasonable
   wait, callers that cannot afford to fail - an on-demand analysis, as opposed to
   a precomputed site that can simply be rebuilt later - are expected to try
   another soil source rather than to retry harder. See `_load_soil`.

2. At the Sangrur pilot point SoilGrids predicts 1.3 percent topsoil organic carbon.
   Punjab's intensively cropped alluvial topsoils are repeatedly reported in the
   0.3-0.5 percent range. That is a factor of three in the quantity that
   initialises the RothC pools, so the carbon objective for a SoilGrids site is
   reported with its confidence downgraded and this discrepancy named. We do not
   quietly substitute a literature value: the number is what the global product
   says, and the caveat travels with it.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path

from ..data.soils import SoilTexture, classify_texture

SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"

# The properties we need, and nothing more. Each extra property slows the query and
# raises the chance of being throttled.
PROPERTIES = ("clay", "sand", "silt", "soc", "bdod", "wv0033", "wv1500", "cfvo")

# SoilGrids standard depth intervals, in order.
DEPTHS = ("0-5cm", "5-15cm", "15-30cm", "30-60cm", "60-100cm", "100-200cm")

# Unit conversions. SoilGrids returns integers scaled by a d_factor which the
# response itself reports; we assert against these rather than trusting the
# response, so a silent change of units upstream fails loudly instead of moving
# every number by a decade. (That is exactly how the gridMET packed-integer bug
# got through once already.)
D_FACTORS = {
    "clay": 10,     # g/kg      -> %
    "sand": 10,     # g/kg      -> %
    "silt": 10,     # g/kg      -> %
    "soc": 10,      # dg/kg     -> g/kg
    "bdod": 100,    # cg/cm3    -> kg/dm3
    "wv0033": 10,   # 10^-3     -> vol %
    "wv1500": 10,   # 10^-3     -> vol %
    "cfvo": 10,     # cm3/dm3   -> vol %
}

DEFAULT_PROFILE_DEPTH_CM = 150.0
DEFAULT_SURFACE_DEPTH_CM = 15.0

# Plausibility guards. These are the ranges outside which a value is a unit error
# rather than an unusual soil, which is the failure mode that actually happens.
PLAUSIBLE = {
    "clay_pct": (0.0, 100.0),
    "awc_mm_per_m": (20.0, 320.0),
    "bulk_density_g_cm3": (0.6, 2.0),
    "organic_carbon_pct": (0.01, 25.0),
}


class SoilGridsError(ValueError):
    pass


@dataclass(frozen=True)
class SoilGridsLayer:
    """One SoilGrids depth interval, converted to working units."""

    top_cm: float
    bottom_cm: float
    sand_pct: float
    silt_pct: float
    clay_pct: float
    organic_carbon_pct: float
    bulk_density_g_cm3: float
    theta_fc: float          # volumetric, at 33 kPa
    theta_wp: float          # volumetric, at 1500 kPa
    coarse_fragments_pct: float

    @property
    def thickness_cm(self) -> float:
        return max(self.bottom_cm - self.top_cm, 0.0)

    @property
    def awc_mm_per_m(self) -> float:
        """Plant-available water, corrected for coarse fragments.

        SoilGrids reports water retention for the fine earth fraction, so the
        stones have to be taken out by volume or a gravelly soil is credited with
        storage it does not have.
        """
        fine_earth = max(1.0 - self.coarse_fragments_pct / 100.0, 0.0)
        return 1000.0 * max(self.theta_fc - self.theta_wp, 0.0) * fine_earth


@dataclass(frozen=True)
class SoilGridsProfile:
    """A predicted soil profile at a point."""

    latitude: float
    longitude: float
    layers: tuple[SoilGridsLayer, ...]
    attempts: int = 1

    def _weights(self, depth_cm: float) -> list[tuple[SoilGridsLayer, float]]:
        """Each layer and the thickness of it that falls inside ``depth_cm``."""
        out = []
        for layer in self.layers:
            overlap = min(layer.bottom_cm, depth_cm) - layer.top_cm
            if overlap > 0:
                out.append((layer, overlap))
        if not out:
            raise SoilGridsError(f"no layers within {depth_cm} cm")
        return out

    def depth_weighted(self, field: str, depth_cm: float) -> float:
        parts = self._weights(depth_cm)
        total = sum(w for _, w in parts)
        return sum(getattr(layer, field) * w for layer, w in parts) / total

    def awc_mm_per_m(self, depth_cm: float = DEFAULT_PROFILE_DEPTH_CM) -> float:
        return self.depth_weighted("awc_mm_per_m", depth_cm)

    def topsoil_organic_carbon_pct(self, depth_cm: float = 30.0) -> float:
        return self.depth_weighted("organic_carbon_pct", depth_cm)

    @property
    def described_depth_cm(self) -> float:
        return max((layer.bottom_cm for layer in self.layers), default=0.0)


def _convert(name: str, raw: float) -> float:
    return raw / D_FACTORS[name]


def _parse(payload: dict, latitude: float, longitude: float, attempts: int = 1
           ) -> SoilGridsProfile:
    """Turn a SoilGrids response into a profile, checking units as we go."""
    try:
        raw_layers = payload["properties"]["layers"]
    except (KeyError, TypeError) as exc:
        raise SoilGridsError(f"unexpected SoilGrids response shape: {exc}") from exc

    by_name: dict[str, dict[str, float | None]] = {}
    for layer in raw_layers:
        name = layer["name"]
        if name in D_FACTORS:
            reported = layer.get("unit_measure", {}).get("d_factor")
            if reported is not None and reported != D_FACTORS[name]:
                raise SoilGridsError(
                    f"SoilGrids changed the scaling of {name}: response says "
                    f"d_factor={reported}, this adapter assumes {D_FACTORS[name]}. "
                    "Every value of this property would be wrong by that ratio, so "
                    "refusing rather than converting."
                )
        by_name[name] = {
            d["label"]: d["values"].get("mean") for d in layer["depths"]
        }

    missing = [p for p in PROPERTIES if p not in by_name]
    if missing:
        raise SoilGridsError(f"SoilGrids response lacks {missing}")

    layers: list[SoilGridsLayer] = []
    for label in DEPTHS:
        values = {p: by_name[p].get(label) for p in PROPERTIES}
        if any(v is None for v in values.values()):
            continue  # a depth the prediction does not cover, or a throttled cell
        top, bottom = label.replace("cm", "").split("-")
        layers.append(
            SoilGridsLayer(
                top_cm=float(top),
                bottom_cm=float(bottom),
                sand_pct=_convert("sand", values["sand"]),
                silt_pct=_convert("silt", values["silt"]),
                clay_pct=_convert("clay", values["clay"]),
                # soc converts to g/kg; percent is g/kg / 10.
                organic_carbon_pct=_convert("soc", values["soc"]) / 10.0,
                bulk_density_g_cm3=_convert("bdod", values["bdod"]),
                theta_fc=_convert("wv0033", values["wv0033"]) / 100.0,
                theta_wp=_convert("wv1500", values["wv1500"]) / 100.0,
                coarse_fragments_pct=_convert("cfvo", values["cfvo"]),
            )
        )

    if not layers:
        raise SoilGridsError(
            "SoilGrids answered with no usable values at this point. This is "
            "normally throttling rather than missing data - the same point often "
            "succeeds on retry - so this is reported as a failure to fetch, not as "
            "an absence of soil."
        )
    return SoilGridsProfile(
        latitude=latitude, longitude=longitude,
        layers=tuple(layers), attempts=attempts,
    )


def fetch_soilgrids_point(
    latitude: float,
    longitude: float,
    *,
    cache_path: str | None = None,
    max_attempts: int = 6,
    backoff_s: float = 6.0,
    timeout_s: float = 120.0,
) -> SoilGridsProfile:
    """Fetch the predicted soil profile at a point, with retry.

    The retry is not defensive padding. ISRIC rate-limits by returning HTTP 200
    with every value null, so "no data" and "slow down" are the same response on
    the wire. Accepting the first 200 would have reported most of the world as
    having no soil - eight of ten test points, on the first pass.
    """
    if cache_path:
        path = Path(cache_path)
        if path.exists():
            return _parse(json.loads(path.read_text()), latitude, longitude)

    query = "&".join(
        [f"lon={longitude}", f"lat={latitude}"]
        + [f"property={p}" for p in PROPERTIES]
        + [f"depth={d}" for d in DEPTHS]
        + ["value=mean"]
    )
    url = f"{SOILGRIDS_URL}?{query}"

    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as response:
                payload = json.load(response)
            profile = _parse(payload, latitude, longitude, attempts=attempt)
        except (urllib.error.URLError, OSError, json.JSONDecodeError,
                SoilGridsError) as exc:
            last = exc
        else:
            if cache_path:
                path = Path(cache_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload))
            return profile
        if attempt < max_attempts:
            time.sleep(backoff_s * attempt)

    raise SoilGridsError(
        f"SoilGrids gave no usable answer for ({latitude}, {longitude}) after "
        f"{max_attempts} attempts. Last failure: {last}"
    )


def texture_from_soilgrids(
    profile: SoilGridsProfile,
    centroid_lookup,
    *,
    depth_cm: float = DEFAULT_PROFILE_DEPTH_CM,
    surface_depth_cm: float = DEFAULT_SURFACE_DEPTH_CM,
) -> SoilTexture:
    """Build a SoilTexture backed by SoilGrids predictions.

    The same two-depth split as the SSURGO path: available water is weighted over
    the root zone, texture class and surface properties come from the plough layer.

    One field deliberately stays at the class centroid - ``hydrologic_group``.
    SoilGrids has no equivalent, and the group drives runoff through the curve
    number. Taking the texture centroid's group is a stated fallback, not a
    measurement, and it is the main reason a SoilGrids site's runoff and erosion
    numbers carry lower confidence than a SSURGO site's.
    """
    sand = profile.depth_weighted("sand_pct", surface_depth_cm)
    silt = profile.depth_weighted("silt_pct", surface_depth_cm)
    clay = profile.depth_weighted("clay_pct", surface_depth_cm)

    total = sand + silt + clay
    if not 90.0 <= total <= 110.0:
        raise SoilGridsError(
            f"SoilGrids texture fractions sum to {total:.1f}% at "
            f"({profile.latitude}, {profile.longitude}); that is a unit problem, "
            "not a soil"
        )
    # Renormalise the small rounding residue so the triangle classifier gets a
    # closed composition.
    sand, silt, clay = (100.0 * v / total for v in (sand, silt, clay))

    texture_id = classify_texture(sand, silt, clay)
    base = centroid_lookup(texture_id)

    awc = profile.awc_mm_per_m(depth_cm)
    organic_carbon = profile.topsoil_organic_carbon_pct()
    bulk_density = profile.depth_weighted("bulk_density_g_cm3", surface_depth_cm)

    for key, value in (
        ("awc_mm_per_m", awc),
        ("bulk_density_g_cm3", bulk_density),
        ("organic_carbon_pct", organic_carbon),
    ):
        low, high = PLAUSIBLE[key]
        if not low <= value <= high:
            raise SoilGridsError(
                f"SoilGrids {key} of {value:.3f} is outside the plausible range "
                f"{low}-{high}; treating this as a unit or parsing error rather "
                "than an unusual soil"
            )

    return replace(
        base,
        sand_pct=sand,
        silt_pct=silt,
        clay_pct=clay,
        bulk_density_g_cm3=bulk_density,
        measured_fractions=False,
        measured_awc_mm_per_m=awc,
        awc_provenance="prediction",
        organic_carbon_pct=organic_carbon,
        confidence="medium",
        source=(
            f"SoilGrids v2.0 250 m prediction, {profile.described_depth_cm:.0f} cm "
            "described; hydrologic group from texture class"
        ),
        notes=(
            "Predicted, not surveyed. Water retention is itself a model output, "
            "there is no hydrologic group in the product, and the 250 m cell is "
            "larger than most fields."
        ),
    )
