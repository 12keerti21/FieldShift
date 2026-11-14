"""Regions: the invariants that keep a transfer honest.

The point of the region abstraction is that moving the engine to a new part of the
world cannot quietly inherit the first region's credibility. These tests pin that
down: a region with nothing to validate against must say so, every pattern must fit
its own calendar, and the evidence sources a region declares must be the ones
actually used there.
"""

from __future__ import annotations

import pytest

from fieldshift.api.bundle import SITES
from fieldshift.api.regions import (
    IN_PUNJAB,
    PADDY,
    REGIONS,
    US_HIGH_PLAINS,
    Region,
    RegionError,
    region,
)
from fieldshift.data import load_registry
from fieldshift.decide.candidates import check_calendar, enumerate_rotations
from fieldshift.decide.evidence import CONFIDENCE_ORDER, NO_YIELD_DATA, build_card

REGISTRY = load_registry()


# ------------------------------------------------------------ structure


def test_every_region_loads_and_is_addressable():
    for r in REGIONS:
        assert region(r.id) is r


def test_unknown_region_is_refused_with_the_known_list():
    with pytest.raises(RegionError, match="unknown region"):
        region("mars_crater")


def test_unknown_soil_source_is_refused():
    with pytest.raises(RegionError, match="unknown soil source"):
        Region(
            id="x", name="x", tier=2, patterns=US_HIGH_PLAINS.patterns,
            constraints=US_HIGH_PLAINS.constraints, soil_source="guesswork",
            sources=(), yield_validation=None,
        )


def test_duplicate_pattern_ids_are_refused():
    dup = US_HIGH_PLAINS.patterns + (US_HIGH_PLAINS.patterns[0],)
    with pytest.raises(RegionError, match="duplicate pattern"):
        Region(
            id="x", name="x", tier=2, patterns=dup,
            constraints=US_HIGH_PLAINS.constraints, soil_source="ssurgo",
            sources=(), yield_validation=None,
        )


# ------------------------------------------------------------ the honesty invariant


def test_a_tier_2_region_cannot_claim_yield_validation():
    """The structural guard against the transfer borrowing Tier 1's credibility.

    If someone later obtains a district yield series for Punjab and wires it in,
    this fails until they also promote the region to tier 1 - which is the correct
    order of events, because having the data is what makes it tier 1.
    """
    with pytest.raises(RegionError, match="tier 2 but claims yield validation"):
        Region(
            id="x", name="x", tier=2, patterns=IN_PUNJAB.patterns,
            constraints=IN_PUNJAB.constraints, soil_source="soilgrids",
            sources=(), yield_validation="some county series",
        )


def test_the_us_region_is_validated_and_punjab_is_not():
    assert US_HIGH_PLAINS.is_validated
    assert US_HIGH_PLAINS.tier == 1
    assert not IN_PUNJAB.is_validated
    assert IN_PUNJAB.tier == 2


def test_an_unvalidated_region_cannot_produce_a_high_confidence_card():
    """The downgrade has to be structural, not a disclaimer someone remembered.

    A card takes its confidence from its weakest source, and an unvalidated region
    declares a low-confidence "no yield series" source, so no amount of good
    physics can lift the card.
    """
    card = build_card("test", sources=IN_PUNJAB.sources)
    assert card.confidence == "low"
    assert NO_YIELD_DATA in card.limiting_sources
    # and it must be strictly worse than the validated region's
    us = build_card("test", sources=US_HIGH_PLAINS.sources)
    assert CONFIDENCE_ORDER[card.confidence] < CONFIDENCE_ORDER[us.confidence]


def test_an_unvalidated_region_lists_what_to_check_first():
    """"Unvalidated" is only useful if it comes with the route out of it."""
    assert IN_PUNJAB.review_first
    assert len(IN_PUNJAB.review_first) >= 3
    joined = " ".join(IN_PUNJAB.review_first).lower()
    # the three assumptions the Punjab run actually turns on
    assert "percolation" in joined
    assert "hydrologic group" in joined
    assert "transplanting" in joined or "sowing" in joined


def test_every_region_declares_a_soil_source_it_can_reach():
    """SSURGO stops at the US border, so a non-US region must not claim it."""
    names = {s.name for s in IN_PUNJAB.sources}
    assert "USDA SSURGO" not in names
    assert "USDA NASS QuickStats" not in names
    assert "ISRIC SoilGrids v2.0" in names


# ------------------------------------------------------------ calendars


@pytest.mark.parametrize("r", REGIONS, ids=[r.id for r in REGIONS])
def test_every_single_year_pattern_fits_its_own_calendar(r: Region):
    """A pattern that cannot even be grown alone is a data-entry error.

    Checked cyclically, so a rabi crop sown in November and harvested in April
    must still leave room for the next pass of the same pattern.
    """
    for pattern in r.patterns:
        problem = check_calendar([pattern], REGISTRY)
        assert problem is None, f"{r.id}/{pattern.id}: {problem}"


@pytest.mark.parametrize("r", REGIONS, ids=[r.id for r in REGIONS])
def test_every_region_yields_feasible_rotations(r: Region):
    """A region whose constraints admit nothing is misconfigured, not selective."""
    result = enumerate_rotations(
        r.patterns, registry=REGISTRY, cycle_years=3, constraints=r.constraints
    )
    assert len(result) > 0, f"{r.id} admits no feasible rotation"
    assert result.examined >= len(result)


@pytest.mark.parametrize("r", REGIONS, ids=[r.id for r in REGIONS])
def test_every_crop_a_region_can_place_is_in_the_table(r: Region):
    for pattern in r.patterns:
        for season in pattern.seasons:
            REGISTRY.crop(season.crop_id)  # raises if unknown


def test_punjab_has_two_seasons_in_a_year_and_the_us_has_one():
    """The structural difference the region abstraction exists to express."""
    punjab_multi = [p for p in IN_PUNJAB.patterns if len(p.seasons) >= 2]
    assert len(punjab_multi) >= 8, "Punjab is a double-cropped calendar"
    assert any(len(p.seasons) == 3 for p in IN_PUNJAB.patterns)


def test_punjab_admits_no_fallow_year():
    """On the High Plains a fallow year banks water and is a real option.

    In Punjab land is cropped twice a year and a bare year is not among the things
    a farmer there is choosing between, so leaving it in the candidate set would
    spend the search on options that do not exist.
    """
    assert IN_PUNJAB.constraints.max_fallow_fraction == 0.0
    assert not any(p.is_fallow_year for p in IN_PUNJAB.patterns)
    assert US_HIGH_PLAINS.constraints.max_fallow_fraction > 0.0
    assert any(p.is_fallow_year for p in US_HIGH_PLAINS.patterns)


# ------------------------------------------------------------ the paddy wiring


def test_rice_is_always_ponded_and_nothing_else_is():
    """Rice must reach the ponded balance, and only rice.

    A dry crop routed through the paddy model would be reported as never water
    stressed, which is the most flattering possible error.
    """
    for pattern in IN_PUNJAB.patterns:
        for season in pattern.seasons:
            is_rice = season.crop_id.startswith("rice")
            is_ponded = season.irrigation.mode == "ponded"
            assert is_rice == is_ponded, (
                f"{pattern.id}/{season.crop_id}: ponded={is_ponded}"
            )


def test_punjab_irrigation_is_basin_scale_not_pivot_scale():
    """Flood basins apply much more per pass than a centre pivot, and the
    difference is a real inefficiency of the method rather than a knob."""
    pivot = [
        s.irrigation for p in US_HIGH_PLAINS.patterns for s in p.seasons
        if s.irrigation.is_irrigated
    ]
    punjab = [
        s.irrigation for p in IN_PUNJAB.patterns for s in p.seasons
        if s.irrigation.is_irrigated and s.irrigation.mode != "ponded"
    ]
    assert all(i.max_application_mm <= 30 for i in pivot)
    assert all(i.max_application_mm >= 60 for i in punjab)
    assert all(i.efficiency < 0.85 for i in punjab)


def test_the_paddy_policy_names_its_load_bearing_assumption():
    assert PADDY.mode == "ponded"
    assert PADDY.percolation_mm_day > 0.0
    assert PADDY.establishment_mm > 0.0


# ------------------------------------------------------------ sites


def test_every_site_names_a_known_region():
    for spec in SITES:
        assert spec.region_spec is region(spec.region)


def test_every_site_existing_rotation_exists_in_its_region():
    """A typo here would silently drop the farmer's own rotation from the
    comparison, which is the one row they came to look at."""
    for spec in SITES:
        for pattern_id in spec.existing_rotation:
            spec.region_spec.pattern(pattern_id)  # raises if absent


def test_there_is_at_least_one_site_in_each_region():
    covered = {spec.region for spec in SITES}
    assert covered == {r.id for r in REGIONS}
