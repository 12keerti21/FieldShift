"""SSURGO adapter via USDA Soil Data Access: field-scale soil, no credentials.

This is the adapter that makes Tier 1 genuinely field-scale. Our parameter table
ships USDA texture-class centroids, which are honest priors but can be badly wrong
on water retention: at the Finney County pilot point the Richfield silt loam holds
212 mm/m of plant-available water against the silt-loam centroid's 140 mm/m, so the
centroid understated root-zone storage by 52 percent.

SSURGO reports available water capacity directly as a measured property, so it is
used in preference to anything derived from texture. Organic matter comes through
too, which is what initialises the RothC carbon pools - without it a carbon run has
to start from a guess.

Soil Data Access takes SQL over a REST endpoint and needs no authentication, which
makes it one of the few genuinely frictionless datasets in the challenge list.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path

from ..data.soils import SoilTexture, classify_texture

SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"

# Depth-weight available water to the rooting depth that matters, not the whole
# described profile.
DEFAULT_PROFILE_DEPTH_CM = 150.0

# Texture class comes from the FAO-56 surface evaporation layer (Ze = 0.10-0.15 m),
# because that is the layer the class actually parameterises in this model: TEW,
# REW and the surface drying behaviour. Depth matters here rather than being a
# detail - at the Finney pilot point the top 16 cm is a silt loam (29/53/18) while
# anything weighted past ~20 cm becomes a silty clay loam, since the next horizon
# reaches 39 percent clay. Runoff does not depend on this choice: the curve number
# uses SSURGO's own hydrologic group, which NRCS assigns from the limiting layer.
DEFAULT_SURFACE_DEPTH_CM = 15.0


class SsurgoError(ValueError):
    pass


@dataclass(frozen=True)
class SoilHorizon:
    """One described layer of a soil component."""

    component: str
    component_pct: float
    top_cm: float
    bottom_cm: float
    sand_pct: float
    silt_pct: float
    clay_pct: float
    organic_matter_pct: float
    awc_cm_per_cm: float
    bulk_density_g_cm3: float
    ksat_um_s: float

    @property
    def thickness_cm(self) -> float:
        return max(self.bottom_cm - self.top_cm, 0.0)


@dataclass(frozen=True)
class SsurgoProfile:
    """The dominant soil component at a point, with its described horizons."""

    component: str
    component_pct: float
    hydrologic_group: str
    horizons: tuple[SoilHorizon, ...]
    latitude: float
    longitude: float

    def _weights(self, depth_cm: float) -> list[tuple[SoilHorizon, float]]:
        out = []
        for h in self.horizons:
            top, bottom = h.top_cm, min(h.bottom_cm, depth_cm)
            if bottom > top:
                out.append((h, bottom - top))
        if not out:
            raise SsurgoError("no horizons intersect the requested depth")
        return out

    def depth_weighted(self, field: str, depth_cm: float = DEFAULT_PROFILE_DEPTH_CM) -> float:
        pairs = self._weights(depth_cm)
        total = sum(w for _, w in pairs)
        return sum(getattr(h, field) * w for h, w in pairs) / total

    def awc_mm_per_m(self, depth_cm: float = DEFAULT_PROFILE_DEPTH_CM) -> float:
        """Measured plant-available water, mm per metre of profile.

        SSURGO's awc_r is a volumetric fraction (cm water per cm soil), so a
        depth-weighted mean multiplied by 1000 gives mm/m directly.
        """
        return self.depth_weighted("awc_cm_per_cm", depth_cm) * 1000.0

    def surface_horizon(self) -> SoilHorizon:
        return min(self.horizons, key=lambda h: h.top_cm)

    def topsoil_organic_carbon_pct(self, depth_cm: float = 30.0) -> float:
        """Organic carbon in the plough layer, for RothC initialisation.

        SSURGO reports organic MATTER; the conventional conversion divides by 1.724
        (the van Bemmelen factor). That factor is itself an approximation and varies
        with soil type, so it is applied explicitly rather than folded in silently.
        """
        return self.depth_weighted("organic_matter_pct", depth_cm) / 1.724


def _sda_query(sql: str, timeout_s: float = 90.0) -> list[list]:
    body = json.dumps({"format": "JSON+COLUMNNAME", "query": sql}).encode()
    req = urllib.request.Request(
        SDA_URL, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            payload = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise SsurgoError(f"Soil Data Access returned HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise SsurgoError(f"could not reach Soil Data Access: {e.reason}") from None
    table = payload.get("Table")
    if not table:
        raise SsurgoError(
            "Soil Data Access returned no rows. Outside the SSURGO footprint "
            "(it covers the United States only), or an unmapped area."
        )
    return table


def parse_sda_tables(point_table: list[list], latitude: float, longitude: float) -> SsurgoProfile:
    """Build a profile from a Soil Data Access result table.

    Pure, so it can be tested against a recorded response offline.
    """
    cols = point_table[0]
    rows = [dict(zip(cols, r)) for r in point_table[1:]]
    if not rows:
        raise SsurgoError("no horizon rows in the response")

    def num(value, default=0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    # Keep the single dominant component; mixing components would average away the
    # very field-scale detail we came here for.
    best_pct = max(num(r.get("comppct_r")) for r in rows)
    dominant = [r for r in rows if num(r.get("comppct_r")) == best_pct]
    name = str(dominant[0].get("compname", "unknown"))
    hydgrp = str(dominant[0].get("hydgrp") or "").strip() or "B"

    horizons = []
    for r in dominant:
        top, bottom = num(r.get("hzdept_r")), num(r.get("hzdepb_r"), -1.0)
        if bottom <= top:
            continue
        horizons.append(
            SoilHorizon(
                component=name,
                component_pct=best_pct,
                top_cm=top,
                bottom_cm=bottom,
                sand_pct=num(r.get("sandtotal_r")),
                silt_pct=num(r.get("silttotal_r")),
                clay_pct=num(r.get("claytotal_r")),
                organic_matter_pct=num(r.get("om_r")),
                awc_cm_per_cm=num(r.get("awc_r")),
                bulk_density_g_cm3=num(r.get("dbthirdbar_r")),
                ksat_um_s=num(r.get("ksat_r")),
            )
        )
    if not horizons:
        raise SsurgoError(f"component {name!r} has no usable horizon depths")

    return SsurgoProfile(
        component=name,
        component_pct=best_pct,
        hydrologic_group=hydgrp[0] if hydgrp else "B",
        horizons=tuple(sorted(horizons, key=lambda h: h.top_cm)),
        latitude=latitude,
        longitude=longitude,
    )


def fetch_ssurgo_point(
    latitude: float,
    longitude: float,
    *,
    cache_path: str | Path | None = None,
) -> SsurgoProfile:
    """Fetch the dominant soil component and its horizons at a point."""
    if cache_path is not None and Path(cache_path).exists():
        table = json.loads(Path(cache_path).read_text())["Table"]
        return parse_sda_tables(table, latitude, longitude)

    sql = f"""
    SELECT c.compname, c.comppct_r, c.hydgrp,
           ch.hzdept_r, ch.hzdepb_r,
           ch.sandtotal_r, ch.silttotal_r, ch.claytotal_r,
           ch.om_r, ch.awc_r, ch.dbthirdbar_r, ch.ksat_r
    FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('point({longitude} {latitude})') AS m
    INNER JOIN component AS c ON c.mukey = m.mukey
    INNER JOIN chorizon AS ch ON ch.cokey = c.cokey
    WHERE c.majcompflag = 'Yes'
    ORDER BY c.comppct_r DESC, ch.hzdept_r
    """
    table = _sda_query(sql)
    if cache_path is not None:
        p = Path(cache_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"Table": table}))
    return parse_sda_tables(table, latitude, longitude)


def texture_from_ssurgo(
    profile: SsurgoProfile,
    centroid_lookup,
    *,
    depth_cm: float = DEFAULT_PROFILE_DEPTH_CM,
    surface_depth_cm: float = DEFAULT_SURFACE_DEPTH_CM,
) -> SoilTexture:
    """Build a SoilTexture backed by SSURGO measurements.

    Texture fractions, available water capacity, bulk density, saturated
    conductivity and hydrologic group all come from SSURGO. Field capacity and
    wilting point stay at the class centroid because SSURGO does not report them
    directly - but they are no longer load-bearing, because
    `measured_awc_mm_per_m` overrides the derived TAW.

    Note the two different depths. Available water is weighted over the root zone
    (`depth_cm`); the texture class and the surface properties come from the plough
    layer (`surface_depth_cm`). Using one depth for both would either mis-class the
    surface or understate deep storage. SSURGO's own hydrologic group is taken as
    authoritative over the centroid's, since NRCS assigns it from the limiting
    layer and measured infiltration rather than from texture alone - at the Finney
    pilot point SSURGO says group B where the profile-weighted centroid would have
    said D, a difference that materially changes runoff.

    ``centroid_lookup`` is a callable taking a texture id and returning the class
    centroid, normally ``registry.texture``.
    """
    sand = profile.depth_weighted("sand_pct", surface_depth_cm)
    silt = profile.depth_weighted("silt_pct", surface_depth_cm)
    clay = profile.depth_weighted("clay_pct", surface_depth_cm)
    texture_id = classify_texture(sand, silt, clay)
    base = centroid_lookup(texture_id)

    # SSURGO ksat_r is micrometres per second.
    ksat_mm_h = profile.depth_weighted("ksat_um_s", surface_depth_cm) * 3.6
    return replace(
        base,
        sand_pct=sand,
        silt_pct=silt,
        clay_pct=clay,
        bulk_density_g_cm3=profile.depth_weighted("bulk_density_g_cm3", surface_depth_cm)
        or base.bulk_density_g_cm3,
        ksat_mm_h=ksat_mm_h or base.ksat_mm_h,
        hydrologic_group=profile.hydrologic_group,
        measured_fractions=True,
        measured_awc_mm_per_m=profile.awc_mm_per_m(depth_cm),
        awc_provenance="survey",
        organic_carbon_pct=profile.topsoil_organic_carbon_pct(),
        source=f"SSURGO {profile.component} ({profile.component_pct:.0f}% of map unit)",
    )
