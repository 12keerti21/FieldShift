"""USDA soil texture classes and the water properties derived from them.

Two jobs: turn a texture name into the hydraulic parameters the FAO-56 water
balance needs, and turn measured sand/silt/clay fractions into a texture class so
gridded soil products can be used directly.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

VALID_HYDROLOGIC_GROUPS = {"A", "B", "C", "D"}


@dataclass(frozen=True)
class SoilTexture:
    id: str
    name: str
    sand_pct: float
    silt_pct: float
    clay_pct: float
    theta_sat: float
    theta_fc: float
    theta_wp: float
    rew_mm: float
    ksat_mm_h: float
    bulk_density_g_cm3: float
    hydrologic_group: str
    notes: str
    confidence: str
    measured_fractions: bool = False
    measured_awc_mm_per_m: float | None = None
    organic_carbon_pct: float | None = None
    source: str = "USDA texture class centroid"
    # Where the available-water figure came from. "survey" is a mapped soil
    # component (SSURGO), "prediction" is a global model (SoilGrids), "centroid" is
    # this table's own texture-class value. They are not interchangeable and the
    # distinction has to survive all the way to what the farmer is shown, which is
    # why it is a provenance string rather than a measured/estimated boolean.
    awc_provenance: str = "centroid"

    # ---- derived water properties (FAO-56 chapter 8) ----

    @property
    def taw_mm_per_m(self) -> float:
        """Total available water: the store a crop can draw on, mm per metre of root.

        A measured value always wins over one derived from texture. This is not a
        marginal preference: at the Finney County pilot point SSURGO reports
        212 mm/m against the silt-loam centroid's 140 mm/m, so using the centroid
        understated root-zone storage by 52 percent and inflated both modelled
        water stress and irrigation demand.
        """
        if self.measured_awc_mm_per_m is not None:
            return self.measured_awc_mm_per_m
        return 1000.0 * (self.theta_fc - self.theta_wp)

    @property
    def taw_is_measured(self) -> bool:
        """Whether available water came from a soil survey.

        Read by the evidence card: a farmer being shown an irrigation figure should
        be able to see whether the soil behind it was measured on their field.

        A global model prediction is deliberately NOT measured. SoilGrids also
        populates ``measured_awc_mm_per_m`` - it is the best available number and
        it does override the centroid - but calling a 250 m machine-learning
        output "measured" would overstate it to exactly the person least able to
        check, so provenance is what this reads.
        """
        return self.awc_provenance == "survey"

    @property
    def taw_provenance(self) -> str:
        return self.awc_provenance

    def taw_mm(self, root_depth_m: float) -> float:
        return self.taw_mm_per_m * root_depth_m

    def raw_mm(self, root_depth_m: float, p_depletion: float) -> float:
        """Readily available water: depleted before the crop feels stress (FAO-56 eq 83)."""
        return p_depletion * self.taw_mm(root_depth_m)

    def tew_mm(self, ze_m: float = 0.10) -> float:
        """Total evaporable water in the surface layer (FAO-56 eq 73).

        Half the wilting-point content is used because the top few centimetres dry
        well past wilting point under direct solar exposure - this is what makes a
        bare surface lose water a crop canopy would have retained.
        """
        return 1000.0 * (self.theta_fc - 0.5 * self.theta_wp) * ze_m

    @property
    def drainable_porosity(self) -> float:
        """Air-filled fraction at field capacity; near zero means waterlogging risk."""
        return self.theta_sat - self.theta_fc

    @property
    def is_coarse(self) -> bool:
        return self.sand_pct >= 70.0

    @property
    def is_fine(self) -> bool:
        return self.clay_pct >= 35.0


def classify_texture(sand_pct: float, silt_pct: float, clay_pct: float) -> str:
    """USDA texture class from mineral fractions.

    The published NRCS decision sequence; order of the tests is load-bearing, since
    several class polygons overlap on a single criterion and are separated only by
    being reached in this order.

    Fractions are normalised first: gridded soil products routinely sum to 99 or
    101 after their own rounding, and rejecting those would discard usable data.
    """
    total = sand_pct + silt_pct + clay_pct
    if total <= 0:
        raise ValueError("soil fractions must be positive")
    if not 90.0 <= total <= 110.0:
        raise ValueError(
            f"soil fractions sum to {total:g}, too far from 100 to be a rounding artefact"
        )
    scale = 100.0 / total
    sand, silt, clay = sand_pct * scale, silt_pct * scale, clay_pct * scale

    if silt + 1.5 * clay < 15:
        return "sand"
    if silt + 1.5 * clay >= 15 and silt + 2 * clay < 30:
        return "loamy_sand"
    if (7 <= clay < 20 and sand > 52 and silt + 2 * clay >= 30) or (
        clay < 7 and silt < 50 and silt + 2 * clay >= 30
    ):
        return "sandy_loam"
    if silt >= 50 and 12 <= clay < 27:
        return "silt_loam"
    if 50 <= silt < 80 and clay < 12:
        return "silt_loam"
    if silt >= 80 and clay < 12:
        return "silt"
    if 7 <= clay < 27 and 28 <= silt < 50 and sand <= 52:
        return "loam"
    if 20 <= clay < 35 and silt < 28 and sand > 45:
        return "sandy_clay_loam"
    if 27 <= clay < 40 and 20 < sand <= 45:
        return "clay_loam"
    if 27 <= clay < 40 and sand <= 20:
        return "silty_clay_loam"
    if clay >= 35 and sand > 45:
        return "sandy_clay"
    if clay >= 40 and silt >= 40:
        return "silty_clay"
    if clay >= 40 and sand <= 45 and silt < 40:
        return "clay"

    raise ValueError(  # pragma: no cover - the triangle is exhaustive
        f"no USDA class for sand={sand:g} silt={silt:g} clay={clay:g}"
    )


def texture_with_measured_fractions(
    base: SoilTexture, sand_pct: float, silt_pct: float, clay_pct: float
) -> SoilTexture:
    """Class-centroid hydraulics, but the real measured fractions.

    Clay percentage drives the RothC CO2 partition and the RUSLE K factor directly,
    so a measured value must never be discarded in favour of a centroid. The
    hydraulic properties stay at the class centroid because deriving those from
    texture alone needs a pedotransfer function, which is a separate decision.
    ``measured_fractions`` records which is which so an evidence card can say so.
    """
    total = sand_pct + silt_pct + clay_pct
    scale = 100.0 / total
    return replace(
        base,
        sand_pct=sand_pct * scale,
        silt_pct=silt_pct * scale,
        clay_pct=clay_pct * scale,
        measured_fractions=True,
    )
