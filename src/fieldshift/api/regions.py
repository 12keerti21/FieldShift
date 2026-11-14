"""Regions: the cropping calendar, the soil source, and what can be checked.

A region is the thing that makes the engine portable. Everything below this module
- the water balance, the carbon model, the erosion estimate, the rotation search -
is physics and arithmetic that does not know or care where it is. What changes
between the High Plains and Punjab is not the model. It is:

    1. which year patterns are agronomically real, and on what dates
    2. where the soil comes from, because soil surveys are national
    3. what independent data exists to check the answer against

Separating those three from the engine is what turns "this could transfer" into
"this transferred, here is the run". It is also what keeps the transfer honest: a
region declares its own evidence sources, and because an evidence card takes its
confidence from its weakest source, a region with no yield data to check against
cannot report high confidence no matter how good the physics is. The downgrade is
structural rather than a disclaimer someone remembered to write.

The two regions here are deliberately asymmetric, and that asymmetry is the point:

    us_high_plains   SSURGO field-scale soil, NASS county yields to validate
                     against, gridMET to check reference ET. Validated.
    in_punjab        SoilGrids 250 m predicted soil, no public district yield
                     series we could obtain, no independent ET product. Transferred
                     but unvalidated, and it says so on every card.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..agro.rotation import SeasonPlan
from ..agro.waterbalance import IrrigationPolicy
from ..decide.candidates import RotationConstraints, YearPattern
from ..decide.evidence import (
    CROP_TABLE,
    DataSource,
    GRIDMET,
    INDIAWRIS,
    MODIS_NDVI,
    NASADEM,
    NO_YIELD_DATA,
    POWER,
    QUICKSTATS,
    SOILGRIDS,
    SSURGO,
)

# --------------------------------------------------------------------------- #
# Irrigation policies, by how water is actually delivered
# --------------------------------------------------------------------------- #

# Centre pivot on the High Plains: frequent, small, efficient applications.
PIVOT = IrrigationPolicy(
    mode="refill_on_stress", efficiency=0.85,
    refill_to_depletion_frac=0.25, max_application_mm=25,
)

# Punjab tubewell and field channel. Flood-basin application, so each irrigation
# is large and infrequent where a pivot is small and frequent, and conveyance plus
# application losses are higher. Both numbers change the answer: a 75 mm basin
# application onto a profile that can hold 40 mm loses the difference, which is a
# real inefficiency of the method rather than a modelling artefact.
TUBEWELL = IrrigationPolicy(
    mode="refill_on_stress", efficiency=0.70,
    refill_to_depletion_frac=0.30, max_application_mm=75,
)

# Mid-South furrow irrigation: gravity water down the row. Less efficient than a
# pivot and applied in large sets, but it is what the Delta actually uses.
FURROW = IrrigationPolicy(
    mode="refill_on_stress", efficiency=0.65,
    refill_to_depletion_frac=0.30, max_application_mm=65,
)

# Arkansas Grand Prairie rice. Dry-drilled then permanently flooded, so the
# management is the same submergence as Punjab - which is the entire reason this
# site exists. The one number that genuinely differs is percolation: rice is
# grown on the Grand Prairie precisely BECAUSE a dense clay subsoil holds water,
# where Punjab's lighter alluvium lets it through. That difference is the
# hypothesis this site tests, so it is declared here and swept in the validation
# rather than tuned until the answer agreed.
DELTA_FLOOD = IrrigationPolicy(
    mode="ponded", efficiency=0.75,
    percolation_mm_day=2.0, establishment_mm=120.0,
    target_ponding_mm=75.0, min_ponding_mm=50.0, bund_height_mm=125.0,
    max_application_mm=75.0,
)

# Transplanted paddy under continuous submergence. This dispatches to the ponded
# balance, which is a different model - see run_ponded_balance. percolation_mm_day
# is the single most load-bearing number in the whole Punjab run.
PADDY = IrrigationPolicy(
    mode="ponded", efficiency=0.70,
    percolation_mm_day=4.0, establishment_mm=180.0,
    target_ponding_mm=50.0, min_ponding_mm=20.0, bund_height_mm=100.0,
    max_application_mm=75.0,
)


# --------------------------------------------------------------------------- #
# Region
# --------------------------------------------------------------------------- #


class RegionError(ValueError):
    pass


@dataclass(frozen=True)
class Region:
    """One agro-climatic region: its calendar, its data, and its limits."""

    id: str
    name: str
    tier: int
    patterns: tuple[YearPattern, ...]
    constraints: RotationConstraints
    soil_source: str  # "ssurgo" | "soilgrids"
    sources: tuple[DataSource, ...]
    # What independent observation the model was checked against here, or None.
    # None is not a gap to be filled in later with words; it is the reason the
    # evidence cards for this region cannot read "high".
    yield_validation: str | None
    # Assumptions a local agronomist should check first, most important first.
    # These are listed because they are the fastest route from "transferred" to
    # "validated" - each one is a question a person in the district can answer.
    review_first: tuple[str, ...] = ()
    note: str = ""

    def __post_init__(self) -> None:
        if self.soil_source not in ("ssurgo", "soilgrids"):
            raise RegionError(f"unknown soil source {self.soil_source!r}")
        if not self.patterns:
            raise RegionError(f"region {self.id} has no year patterns")
        ids = [p.id for p in self.patterns]
        if len(ids) != len(set(ids)):
            raise RegionError(f"region {self.id} has duplicate pattern ids")
        if self.tier == 2 and self.yield_validation is not None:
            raise RegionError(
                f"region {self.id} is tier 2 but claims yield validation; a "
                "validated region is tier 1 by definition"
            )

    def pattern(self, pattern_id: str) -> YearPattern:
        for p in self.patterns:
            if p.id == pattern_id:
                return p
        raise RegionError(f"region {self.id} has no pattern {pattern_id!r}")

    @property
    def is_validated(self) -> bool:
        return self.yield_validation is not None


# --------------------------------------------------------------------------- #
# Tier 1: US High Plains
# --------------------------------------------------------------------------- #

# Winter wheat is sown in October and harvested the following July, so a wheat year
# leaves no room for a summer crop in the year after it - the calendar check
# enforces that rather than trusting us.
HIGH_PLAINS_PATTERNS: tuple[YearPattern, ...] = (
    YearPattern("fallow", "fallow", (), "banks water, loses carbon and nitrogen"),
    YearPattern(
        "wheat", "winter wheat",
        (SeasonPlan("wheat_winter", 274, 285, fertiliser_n_kg_ha=70.0),),
        "dryland, 70 kg N/ha",
    ),
    YearPattern(
        "wheat_irr", "winter wheat (irrigated)",
        (SeasonPlan("wheat_winter", 274, 285, irrigation=PIVOT,
                    fertiliser_n_kg_ha=100.0),),
        "centre pivot",
    ),
    YearPattern(
        "sorghum", "grain sorghum",
        (SeasonPlan("sorghum", 152, 125, fertiliser_n_kg_ha=50.0),),
        "dryland, drought tolerant",
    ),
    YearPattern(
        "corn_irr", "corn (irrigated)",
        (SeasonPlan("maize", 125, 150, irrigation=PIVOT, fertiliser_n_kg_ha=200.0),),
        "the thirstiest option here",
    ),
    YearPattern(
        "soy_irr", "soybean (irrigated)",
        (SeasonPlan("soybean", 140, 140, irrigation=PIVOT),),
        "fixes its own nitrogen",
    ),
    YearPattern(
        "sorg_cover", "sorghum + rye cover",
        (SeasonPlan("sorghum", 152, 125, fertiliser_n_kg_ha=50.0),
         SeasonPlan("cereal_rye_cover", 288, 70)),
        "a cover crop closes the autumn window",
    ),
    YearPattern(
        "millet", "pearl millet",
        (SeasonPlan("pearl_millet", 152, 100, fertiliser_n_kg_ha=40.0),),
        "lowest water use of any grain here",
    ),
)

US_HIGH_PLAINS = Region(
    id="us_high_plains",
    name="US High Plains",
    tier=1,
    patterns=HIGH_PLAINS_PATTERNS,
    constraints=RotationConstraints(
        max_consecutive_fallow_years=1, max_fallow_fraction=0.5,
        max_irrigated_years=2,
    ),
    soil_source="ssurgo",
    sources=(POWER, SSURGO, NASADEM, CROP_TABLE, QUICKSTATS, GRIDMET),
    yield_validation=(
        "USDA NASS county yields, 1984-2024, four counties in three states: "
        "modelled water stress against detrended yield, 11/11 positive, 8/11 "
        "significant, mean r = +0.447"
    ),
    note=(
        "Semi-arid cropping over the Ogallala aquifer. Water is the binding "
        "constraint and the aquifer is finite."
    ),
)


# --------------------------------------------------------------------------- #
# Tier 2: Indian Punjab
# --------------------------------------------------------------------------- #

# The Punjab calendar is two distinct seasons in one year, which is the structural
# difference from the High Plains: kharif on the monsoon from June, rabi on stored
# soil water and irrigation from November. Rabi wheat crosses the calendar year, so
# the cyclic calendar check matters here for the same reason it does for US winter
# wheat.
#
# Sowing dates are district practice for central Punjab, and durations are field
# occupancy from transplanting or sowing to harvest - not nursery-to-harvest. Both
# are in `review_first` because they are the first thing a local agronomist would
# correct and a two-week shift in transplanting date moves rice water use
# materially.
#
# Nitrogen rates are the regional recommended doses in round numbers. They are an
# input, not a result, and they are listed for review rather than presented as
# sourced values.
PUNJAB_PATTERNS: tuple[YearPattern, ...] = (
    YearPattern(
        "rice_wheat", "rice - wheat",
        (SeasonPlan("rice_lowland", 166, 125, irrigation=PADDY,
                    fertiliser_n_kg_ha=150.0),
         SeasonPlan("wheat", 314, 150, irrigation=TUBEWELL,
                    fertiliser_n_kg_ha=125.0)),
        "the dominant system, and the one drawing the water table down",
    ),
    YearPattern(
        "basmati_wheat", "basmati rice - wheat",
        (SeasonPlan("rice_lowland", 191, 115, irrigation=PADDY,
                    fertiliser_n_kg_ha=100.0),
         SeasonPlan("wheat", 325, 145, irrigation=TUBEWELL,
                    fertiliser_n_kg_ha=125.0)),
        "transplanted a month later, so more of the crop runs on the monsoon",
    ),
    YearPattern(
        "maize_wheat", "maize - wheat",
        (SeasonPlan("maize", 171, 110, irrigation=TUBEWELL,
                    fertiliser_n_kg_ha=150.0),
         SeasonPlan("wheat", 314, 150, irrigation=TUBEWELL,
                    fertiliser_n_kg_ha=125.0)),
        "the main promoted alternative to rice",
    ),
    YearPattern(
        "cotton_wheat", "cotton - wheat",
        (SeasonPlan("cotton", 121, 185, irrigation=TUBEWELL,
                    fertiliser_n_kg_ha=100.0),
         SeasonPlan("wheat", 318, 145, irrigation=TUBEWELL,
                    fertiliser_n_kg_ha=125.0)),
        "the south-western Punjab system",
    ),
    YearPattern(
        "maize_mustard", "maize - mustard",
        (SeasonPlan("maize", 171, 110, irrigation=TUBEWELL,
                    fertiliser_n_kg_ha=150.0),
         SeasonPlan("mustard", 293, 130, irrigation=TUBEWELL,
                    fertiliser_n_kg_ha=100.0)),
        "an oilseed rabi instead of wheat",
    ),
    YearPattern(
        "maize_potato", "maize - potato",
        (SeasonPlan("maize", 171, 110, irrigation=TUBEWELL,
                    fertiliser_n_kg_ha=150.0),
         SeasonPlan("potato", 283, 90, irrigation=TUBEWELL,
                    fertiliser_n_kg_ha=200.0)),
        "high value, high nitrogen, and it leaves the soil bare in spring",
    ),
    YearPattern(
        "pigeonpea", "pigeonpea",
        (SeasonPlan("pigeonpea", 166, 180, irrigation=TUBEWELL),),
        "occupies the whole year and fixes its own nitrogen",
    ),
    YearPattern(
        "groundnut_wheat", "groundnut - wheat",
        (SeasonPlan("groundnut", 171, 130),
         SeasonPlan("wheat", 318, 145, irrigation=TUBEWELL,
                    fertiliser_n_kg_ha=125.0)),
        "a rainfed kharif legume, unusually tolerant of deficit",
    ),
    YearPattern(
        "gm_rice_wheat", "green manure - rice - wheat",
        (SeasonPlan("sesbania", 105, 55),
         SeasonPlan("rice_lowland", 166, 125, irrigation=PADDY,
                    fertiliser_n_kg_ha=100.0),
         SeasonPlan("wheat", 314, 150, irrigation=TUBEWELL,
                    fertiliser_n_kg_ha=125.0)),
        "dhaincha before rice, substituting for part of the nitrogen",
    ),
    YearPattern(
        "mung_basmati_wheat", "summer mungbean - basmati - wheat",
        (SeasonPlan("mungbean", 110, 65),
         SeasonPlan("rice_lowland", 191, 115, irrigation=PADDY,
                    fertiliser_n_kg_ha=100.0),
         SeasonPlan("wheat", 325, 145, irrigation=TUBEWELL,
                    fertiliser_n_kg_ha=125.0)),
        "three crops in the year; only the late basmati leaves room for the mung",
    ),
)

IN_PUNJAB = Region(
    id="in_punjab",
    name="Indian Punjab",
    tier=2,
    patterns=PUNJAB_PATTERNS,
    constraints=RotationConstraints(
        # No fallow year. On the High Plains a fallow year banks water and is a
        # real option; in Punjab, land is cropped twice a year and a bare year is
        # not a choice any farmer here is choosing between. Leaving fallow in the
        # candidate set would have the search spend its time on options that do not
        # exist.
        max_consecutive_fallow_years=0,
        max_fallow_fraction=0.0,
        # Every option is irrigated, so capping irrigated years would eliminate
        # the entire candidate set rather than constrain it.
        max_irrigated_years=None,
        enforce_return_intervals=True,
    ),
    soil_source="soilgrids",
    sources=(POWER, SOILGRIDS, INDIAWRIS, MODIS_NDVI, CROP_TABLE,
             NO_YIELD_DATA),
    yield_validation=None,
    review_first=(
        "Canal supply. This model assumes every millimetre of irrigation is "
        "lifted from the aquifer. Sangrur receives canal water too, and canal "
        "seepage recharges the aquifer, which is the largest single candidate for "
        "the 5-13x gap between modelled draft and observed water-table decline.",
        "Transplanting and sowing dates. These are central-Punjab practice in "
        "round numbers; a two-week shift in the rice transplanting date changes "
        "seasonal water use materially, and the state's own date restrictions have "
        "moved over the period simulated.",
        "Percolation under puddled paddy, held at 4 mm/day. This is the largest "
        "single assumption in the Punjab run and it depends on whether a plough "
        "pan has formed. Local measurement would change the rice water figure more "
        "than any other input.",
        "The hydrologic group. SoilGrids has none, so it falls back to the texture "
        "class, which puts this point in group D. Punjab's alluvial soils are "
        "usually reported as lighter than that, and group D overstates monsoon "
        "runoff - which penalises the rainfed options against the ponded ones.",
        "Nitrogen rates, entered as regional recommended doses rather than what "
        "this farmer applies. Punjab application is widely reported to exceed the "
        "recommendation for rice and wheat.",
        "Yield response factors for pigeonpea, mungbean and sesame, which have no "
        "FAO-33 entry and use a pulse analogue of Ky = 0.9.",
    ),
    note=(
        "Rice-wheat over a falling water table. The same shape of problem as the "
        "Ogallala - a profitable monoculture mining a finite aquifer - which is "
        "why it is the transfer case worth running."
    ),
)


# --------------------------------------------------------------------------- #
# Tier 1: US Mid-South — the region that makes the paddy model checkable
# --------------------------------------------------------------------------- #

# This region exists for one reason: the paddy water balance is the least tested
# thing in the engine, and Punjab has no yield or water record we could obtain to
# test it against. Arkansas floods its rice exactly the way Punjab does, and NASS
# publishes both county rice yields back to 1938 and the irrigation water
# actually applied. The management is the analogue; the climate deliberately is
# not. A paddy model that works in a temperate Arkansas summer AND a tropical
# Punjab monsoon is better evidenced than one that only ever saw one of them.
#
# It also widens the envelope. The four High Plains fields span aridity 1.6 to
# 3.6 - all semi-arid. Arkansas sits at 0.88, so it is the first humid site the
# engine has ever been asked about.
MID_SOUTH_PATTERNS: tuple[YearPattern, ...] = (
    YearPattern(
        "rice", "rice (flooded)",
        (SeasonPlan("rice_lowland", 110, 140, irrigation=DELTA_FLOOD,
                    fertiliser_n_kg_ha=165.0),),
        "drill-seeded in April, permanently flooded, harvested in September",
    ),
    YearPattern(
        "soy", "soybean",
        (SeasonPlan("soybean", 140, 130),),
        "rainfed; the usual partner to rice in this rotation",
    ),
    YearPattern(
        "soy_irr", "soybean (furrow irrigated)",
        (SeasonPlan("soybean", 140, 130, irrigation=FURROW),),
        "most Delta soybean is irrigated at least once",
    ),
    YearPattern(
        "corn_irr", "corn (furrow irrigated)",
        (SeasonPlan("maize", 100, 145, irrigation=FURROW,
                    fertiliser_n_kg_ha=220.0),),
        "the high-input break crop",
    ),
    YearPattern(
        "grain_sorghum", "grain sorghum",
        (SeasonPlan("sorghum", 130, 125, fertiliser_n_kg_ha=110.0),),
        "a drier-footed summer grain",
    ),
    YearPattern(
        "wheat", "winter wheat",
        (SeasonPlan("wheat_winter", 300, 215, fertiliser_n_kg_ha=110.0),),
        "sown after rice harvest, off in time for a summer crop",
    ),
    YearPattern(
        "rice_cover", "rice + winter cover",
        (SeasonPlan("rice_lowland", 110, 140, irrigation=DELTA_FLOOD,
                    fertiliser_n_kg_ha=165.0),
         SeasonPlan("cereal_rye_cover", 280, 80)),
        "a cover crop closes the winter window after rice",
    ),
)

US_MID_SOUTH = Region(
    id="us_mid_south",
    name="US Mid-South",
    tier=1,
    patterns=MID_SOUTH_PATTERNS,
    constraints=RotationConstraints(
        # A bare year is not a Delta option; the land is cropped every year.
        max_consecutive_fallow_years=0,
        max_fallow_fraction=0.0,
        max_irrigated_years=None,
    ),
    soil_source="ssurgo",
    sources=(POWER, SSURGO, NASADEM, CROP_TABLE, QUICKSTATS, GRIDMET),
    yield_validation=(
        "USDA NASS: county rice yields from 1938, and state irrigation water "
        "applied to rice (2013, 2018, 2023) - the only observed record we have "
        "found anywhere against which the ponded paddy water balance can be "
        "checked directly"
    ),
    review_first=(
        "Percolation under the permanent flood, declared at 2.0 mm/day against "
        "Punjab's 4.0. The Grand Prairie is rice country precisely because a "
        "dense clay subsoil holds water, so a lower figure is expected - but it "
        "is the single number the irrigation estimate turns on and it is swept "
        "rather than asserted in scripts/validate_paddy.py.",
        "Flood timing. Delta rice is drill-seeded dry in April and permanently "
        "flooded around the four-leaf stage, whereas this model floods from "
        "establishment. The first few weeks are therefore wetter in the model "
        "than in the field.",
    ),
    note=(
        "Grand Prairie rice and soybean on a clay-pan soil. Humid where every "
        "other field we run is semi-arid, and flooded exactly as Punjab is - "
        "which is what makes the paddy model testable here."
    ),
)


REGIONS: tuple[Region, ...] = (US_HIGH_PLAINS, US_MID_SOUTH, IN_PUNJAB)
REGIONS_BY_ID = {r.id: r for r in REGIONS}


def region(region_id: str) -> Region:
    try:
        return REGIONS_BY_ID[region_id]
    except KeyError:
        raise RegionError(
            f"unknown region {region_id!r}; known: {sorted(REGIONS_BY_ID)}"
        ) from None
