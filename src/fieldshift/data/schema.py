"""Typed views over the crop and pathogen parameter tables.

The JSON files are the source of truth and are meant to be edited by hand (and by
agronomists). These dataclasses exist so the rest of the engine gets attribute
access and a validation boundary, not to replace the JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Kc:
    """Crop coefficients at the three anchor points of the FAO-56 curve.

    Used for both the single coefficient (Table 12, includes soil evaporation) and
    the basal coefficient (Table 17, transpiration only). They are NOT
    interchangeable: the dual-coefficient water balance must use the basal values,
    or soil evaporation is counted twice.
    """

    ini: float
    mid: float
    end: float


@dataclass(frozen=True)
class Stages:
    """FAO-56 four-stage growth lengths, days."""

    ini: int
    dev: int
    mid: int
    late: int

    @property
    def total(self) -> int:
        return self.ini + self.dev + self.mid + self.late


@dataclass(frozen=True)
class Root:
    initial_depth_m: float
    max_depth_m: float
    p_depletion: float
    height_m: float


@dataclass(frozen=True)
class Thermal:
    t_base_c: float
    t_opt_min_c: float
    t_opt_max_c: float
    t_max_c: float
    gdd_maturity_c_days: float
    frost_sensitive: bool
    t_frost_damage_c: float


@dataclass(frozen=True)
class Water:
    seasonal_req_mm: tuple[float, float]

    @property
    def mid(self) -> float:
        return sum(self.seasonal_req_mm) / 2.0


@dataclass(frozen=True)
class Nitrogen:
    is_fixer: bool
    bnf_kg_n_ha: tuple[float, float]
    ndfa_fraction: float
    n_uptake_kg_per_t_product: float

    @property
    def bnf_mid(self) -> float:
        return sum(self.bnf_kg_n_ha) / 2.0


@dataclass(frozen=True)
class Residue:
    product_to_residue_ratio: float
    cn_ratio: float
    root_shoot_ratio: float
    decomposition: str
    dpm_rpm_ratio: float


@dataclass(frozen=True)
class Traits:
    drought_tolerance: int
    heat_tolerance: int
    waterlogging_tolerance: int
    salinity_tolerance_ec_ds_m: float
    max_canopy_cover_frac: float
    deep_rooted: bool
    cover_crop: bool
    biofumigant: bool
    allelopathic: bool
    nematode_suppressive: bool
    forage_value: int


@dataclass(frozen=True)
class RotationInfo:
    min_return_interval_agronomic_years: int
    notes: str


@dataclass(frozen=True)
class YieldResponse:
    """FAO-33 Ky: how hard yield falls when water use falls short.

    `value` is None where the crop has no marketable yield, which is a meaningful
    state rather than missing data - a green manure's benefit is never a yield.
    """

    value: float | None
    confidence: str
    source: str

    @property
    def is_known(self) -> bool:
        return self.value is not None

    def relative_yield_loss(self, et_deficit_fraction: float) -> float | None:
        """Fraction of attainable yield lost, from the relative ET deficit."""
        if self.value is None:
            return None
        return min(max(self.value * et_deficit_fraction, 0.0), 1.0)


@dataclass(frozen=True)
class Economics:
    yield_t_ha: tuple[float, float, float]
    price_local_required: bool
    # Dry matter as a fraction of the MARKETED yield, which is how the crop is
    # weighed and sold: grain near 13 percent moisture, potato near 79, fresh
    # forage higher still.
    #
    # It exists because summing marketed tonnes across crops is only meaningful
    # when they carry comparable water. Potato at 25 t/ha of fresh tubers against
    # wheat at 4 t/ha of grain is a six-fold difference in sale weight and
    # roughly a 1.5-fold difference in dry matter; a production objective that
    # added them raw made a maize-potato rotation look 2.5x more productive than
    # rice-wheat purely through tuber water. On the US sites every crop was grain,
    # so this was latent until the Punjab region introduced potato.
    dry_matter_frac: float = 1.0

    @property
    def yield_typical(self) -> float:
        return self.yield_t_ha[1]

    @property
    def dry_matter_typical(self) -> float:
        return self.yield_typical * self.dry_matter_frac


@dataclass(frozen=True)
class Meta:
    confidence: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class Crop:
    id: str
    name: str
    family: str
    group: str
    duration_days: int
    multi_year: bool
    typical_seasons: tuple[str, ...]
    kc: Kc  # FAO-56 Table 12, single coefficient - reference only
    kcb: Kc  # FAO-56 Table 17, basal coefficient - what the dual model uses
    stages_days: Stages
    root: Root
    thermal: Thermal
    water: Water
    nitrogen: Nitrogen
    residue: Residue
    traits: Traits
    rotation: RotationInfo
    economics: Economics
    ky: YieldResponse
    meta: Meta

    @property
    def is_fallow(self) -> bool:
        return self.group == "fallow"

    @property
    def provides_ground_cover(self) -> bool:
        return self.traits.max_canopy_cover_frac > 0.0

    def residue_dm_t_ha(self, yield_t_ha: float | None = None) -> float:
        """Above-ground residue dry matter left after harvest, t DM/ha.

        Green manures and cover crops have no marketable product, so their biomass
        cannot be derived from yield; those entries need an explicit biomass figure
        and are returned as 0.0 here until the simulator supplies one.
        """
        if yield_t_ha is None:
            yield_t_ha = self.economics.yield_typical
        return self.residue.product_to_residue_ratio * yield_t_ha


@dataclass(frozen=True)
class Pathogen:
    id: str
    name: str
    organism: str
    type: str
    inoculum: str
    host_crops: tuple[str, ...]
    host_families: tuple[str, ...]
    soil_persistence_years: int
    recommended_return_interval_years: int
    rotation_effective: bool
    rotation_efficacy: str
    presence: str  # widespread | regional | quarantine
    notes: str
    confidence: str

    @property
    def is_regulated(self) -> bool:
        return self.presence == "quarantine"


@dataclass(frozen=True)
class IntervalConstraint:
    """Why a crop cannot return to a field sooner than it can."""

    crop_id: str
    years: int
    driver: str
    driver_kind: str  # "pathogen" | "agronomic"
    efficacy: str
    confidence: str
    presence: str
    pathogen_id: str | None
    explanation: str

    @property
    def is_precautionary(self) -> bool:
        """The interval rests on an organism that may simply not be in this field."""
        return self.presence == "regional"


@dataclass(frozen=True)
class Violation:
    """A rotation sequence that breaks a return-interval constraint."""

    slot_a: int
    slot_b: int
    crop_a: str
    crop_b: str
    gap_years: float
    required_years: int
    driver: str
    driver_kind: str
    efficacy: str
    confidence: str
    presence: str = field(default="widespread")
    presence_confirmed: bool = field(default=False)
    wrapped: bool = field(default=False)

    @property
    def regulatory(self) -> bool:
        """A regulated pest: the farmer needs the plant protection authority, not us.

        Kept separate from severity on purpose - being a legal matter and being
        agronomically severe are different axes and must not be collapsed.
        """
        return self.presence == "quarantine"

    @property
    def severity(self) -> str:
        """How badly the constraint is broken, tempered by what we actually know.

        Two deliberate downgrades to 'caution':

        - weak or no rotation efficacy: for a broad host-range organism no practical
          rotation delivers a clean break, so calling the breach fatal is misleading.
        - an unconfirmed 'regional' organism: we have no evidence it is in this field.
          Reporting it is right; blocking a rotation on it is not, and that line is
          what keeps the tool advisory rather than prescriptive.
        """
        if self.efficacy in ("weak", "none"):
            return "caution"
        if self.presence == "regional" and not self.presence_confirmed:
            return "caution"
        shortfall = self.required_years - self.gap_years
        if shortfall >= 2:
            return "high"
        return "moderate"

    @property
    def is_blocking(self) -> bool:
        return self.severity == "high"

    def describe(self) -> str:
        where = "wrapping around the rotation cycle" if self.wrapped else "in sequence"
        text = (
            f"{self.crop_b} returns {self.gap_years:g} year(s) after {self.crop_a} "
            f"{where}, but {self.driver} needs {self.required_years} year(s). "
            f"Rotation efficacy against it: {self.efficacy}; confidence: {self.confidence}."
        )
        if self.presence == "regional" and not self.presence_confirmed:
            text += (
                " This organism is patchily distributed and has not been confirmed "
                "locally, so treat it as a question for an extension agronomist "
                "rather than a reason to reject the rotation."
            )
        if self.regulatory:
            text += (
                " This is a regulated quarantine pest - if it is present, official "
                "guidance takes precedence over anything this tool suggests."
            )
        return text
