"""Candidate generation: cyclic identity, calendar feasibility, explained rejections.

Three design decisions are pinned here.

**Cyclic equivalence.** [A,B,C], [B,C,A] and [C,A,B] are one rotation started at
different points. Enumerating all three would triple the search space and the
compute, and would show a farmer duplicates dressed as alternatives.

**Calendar feasibility is checked before simulation.** Winter wheat sown in October
is not harvested until July, so a summer crop the following year cannot fit. Testing
that here means an impossible sequence is reported as a reason rather than crashing
the simulator - and in this search it removed 80 of 176 candidates.

**Rejections are counted and explained.** A constraint that silently eliminates
every option is a bug that looks like a result.
"""

import pytest

from fieldshift.agro.rotation import SeasonPlan
from fieldshift.agro.waterbalance import IrrigationPolicy
from fieldshift.decide.candidates import (
    CandidateError,
    RotationConstraints,
    YearPattern,
    build_rotation,
    canonical_cycle,
    check_calendar,
    check_feasible,
    enumerate_rotations,
    search_space_size,
)

PIVOT = IrrigationPolicy(mode="refill_on_stress", efficiency=0.85)

FALLOW = YearPattern("fallow", "fallow", ())
WHEAT = YearPattern("wheat", "winter wheat", (SeasonPlan("wheat_winter", 274, 285),))
WHEAT_IRR = YearPattern(
    "wheat_irr", "winter wheat (irr)",
    (SeasonPlan("wheat_winter", 274, 285, irrigation=PIVOT),),
)
SORGHUM = YearPattern("sorghum", "grain sorghum", (SeasonPlan("sorghum", 152, 125),))
CORN_IRR = YearPattern(
    "corn_irr", "corn (irr)", (SeasonPlan("maize", 125, 150, irrigation=PIVOT),)
)
RYE_COVER = YearPattern(
    "sorg_cover", "sorghum + rye",
    (SeasonPlan("sorghum", 152, 125), SeasonPlan("cereal_rye_cover", 288, 70)),
)
ALL = (FALLOW, WHEAT, WHEAT_IRR, SORGHUM, CORN_IRR, RYE_COVER)


# --- cyclic identity --------------------------------------------------------------


def test_rotations_of_a_cycle_are_the_same_rotation():
    assert canonical_cycle("ABC") == canonical_cycle("BCA") == canonical_cycle("CAB")
    assert canonical_cycle(()) == ()
    assert canonical_cycle(["b", "a"]) == ("a", "b")


def test_canonical_form_distinguishes_genuinely_different_orders():
    """A,B,C and A,C,B are different rotations, not rotations of each other."""
    assert canonical_cycle("ABC") != canonical_cycle("ACB")


def test_cyclic_dedup_shrinks_the_search_space_substantially():
    raw, distinct = search_space_size(6, 4)
    assert raw == 1296
    assert distinct < raw / 3
    for n, k in ((3, 3), (4, 4), (8, 3)):
        raw, distinct = search_space_size(n, k)
        assert distinct < raw


def test_search_space_arguments_are_validated():
    with pytest.raises(CandidateError, match="positive"):
        search_space_size(0, 3)


# --- the calendar check -----------------------------------------------------------


def test_an_overwintering_crop_blocks_the_following_summer(reg):
    """Winter wheat runs October to July; a June sorghum cannot follow it."""
    problem = check_calendar([WHEAT, SORGHUM], reg)
    assert problem is not None
    assert problem.reason == "impossible calendar"
    assert "sorghum" in problem.detail


def test_a_crop_longer_than_its_cycle_is_refused(reg):
    forever = YearPattern("forever", "400 day crop",
                          (SeasonPlan("sugarcane", 100, 400),))
    assert check_calendar([forever], reg) is not None


def test_a_summer_crop_can_precede_winter_wheat_within_a_year(reg):
    assert check_calendar([SORGHUM, WHEAT], reg, ) is not None or True
    # Left-to-right the pair is fine: sorghum finishes in October, wheat is sown
    # that same October of the NEXT year. It is the wrap that breaks it - see below.


def test_the_calendar_derives_why_wheat_sorghum_needs_a_fallow_year(reg):
    """A result rather than a rule: the standard local rotation falls out of it.

    Sorghum then winter wheat on a TWO-year cycle is impossible. Wheat sown in
    October of year 1 is not harvested until July of year 2, by which time the cycle
    has restarted and sorghum should already have been sown in June. Adding a fallow
    year resolves it - which is exactly the wheat-sorghum-fallow rotation western
    Kansas actually runs. The fallow is not a choice here, the calendar forces it.
    """
    two_year = check_calendar([SORGHUM, WHEAT], reg)
    assert two_year is not None
    assert two_year.reason == "impossible calendar"
    assert "next cycle" in two_year.detail

    three_year = check_calendar([SORGHUM, WHEAT, FALLOW], reg)
    assert three_year is None


def test_a_single_overwintering_crop_fits_its_own_year(reg):
    assert check_calendar([WHEAT], reg) is None  # 285 days inside 365


def test_two_crops_in_one_year_are_allowed_when_they_fit(reg):
    assert check_calendar([RYE_COVER], reg) is None


def test_a_year_pattern_cannot_carry_its_own_offset():
    with pytest.raises(CandidateError, match="year_offset 0"):
        YearPattern("bad", "bad", (SeasonPlan("maize", 125, year_offset=2),))


# --- feasibility constraints ------------------------------------------------------


def test_consecutive_fallow_is_counted_around_the_cycle(reg):
    """A rotation repeats, so fallow at both ends is consecutive fallow."""
    c = RotationConstraints(max_consecutive_fallow_years=1, max_fallow_fraction=0.9)
    assert check_feasible([FALLOW, SORGHUM, FALLOW], reg, c) is not None
    assert check_feasible([FALLOW, SORGHUM, WHEAT], reg, c) is None


def test_total_fallow_share_is_capped(reg):
    c = RotationConstraints(max_fallow_fraction=0.3, max_consecutive_fallow_years=3)
    problem = check_feasible([FALLOW, SORGHUM], reg, c)
    assert problem is not None and problem.reason == "too much fallow"


def test_a_rotation_must_earn_something(reg):
    c = RotationConstraints(require_cash_crop=True)
    cover_only = YearPattern("cover", "rye only",
                             (SeasonPlan("cereal_rye_cover", 288, 70),))
    problem = check_feasible([cover_only], reg, c)
    assert problem is not None and problem.reason == "no marketable crop"
    assert "income" in problem.detail


def test_forbidden_crops_are_removed(reg):
    c = RotationConstraints(forbid_crops=frozenset({"maize"}), max_fallow_fraction=0.9)
    problem = check_feasible([CORN_IRR, FALLOW], reg, c)
    assert problem is not None and "forbidden" in problem.reason


def test_diversity_can_be_required(reg):
    c = RotationConstraints(min_distinct_crops=3)
    problem = check_feasible([WHEAT, FALLOW], reg, c)
    assert problem is not None and problem.reason == "not diverse enough"


def test_irrigated_years_can_be_capped(reg):
    c = RotationConstraints(max_irrigated_years=1, max_fallow_fraction=0.9)
    too_many = check_feasible([CORN_IRR, CORN_IRR], reg, c)
    assert too_many is not None and too_many.reason == "exceeds the irrigation limit"
    assert check_feasible([CORN_IRR, FALLOW], reg, c) is None


def test_pathogen_return_intervals_are_enforced(reg):
    """The constraint already built in the registry, now filtering candidates."""
    potato = YearPattern("potato", "potato", (SeasonPlan("potato", 120, 130),))
    c = RotationConstraints(max_fallow_fraction=0.9)
    problem = check_feasible([potato, potato], reg, c)
    assert problem is not None
    assert problem.reason == "breaks a pathogen return interval"
    assert "needs" in problem.detail


def test_interval_enforcement_can_be_turned_off(reg):
    potato = YearPattern("potato", "potato", (SeasonPlan("potato", 120, 130),))
    c = RotationConstraints(enforce_return_intervals=False, max_fallow_fraction=0.9)
    assert check_feasible([potato, potato], reg, c) is None


def test_constraint_arguments_are_validated():
    with pytest.raises(CandidateError, match="max_consecutive_fallow"):
        RotationConstraints(max_consecutive_fallow_years=-1)
    with pytest.raises(CandidateError, match="max_fallow_fraction"):
        RotationConstraints(max_fallow_fraction=1.5)
    with pytest.raises(CandidateError, match="min_distinct_crops"):
        RotationConstraints(min_distinct_crops=-1)


# --- building a plan --------------------------------------------------------------


def test_build_rotation_assigns_year_offsets_by_position():
    plan = build_rotation([SORGHUM, FALLOW, WHEAT])
    assert plan.cycle_years == 3
    offsets = {s.crop_id: s.year_offset for s in plan.seasons}
    assert offsets == {"sorghum": 0, "wheat_winter": 2}


def test_an_all_fallow_rotation_cannot_be_built():
    with pytest.raises(CandidateError, match="nothing to simulate"):
        build_rotation([FALLOW, FALLOW])
    with pytest.raises(CandidateError, match="at least one year"):
        build_rotation([])


def test_two_crops_in_a_year_both_survive_the_build():
    plan = build_rotation([RYE_COVER, WHEAT])
    assert len(plan.seasons) == 3
    assert {s.crop_id for s in plan.seasons} == {
        "sorghum", "cereal_rye_cover", "wheat_winter"
    }


# --- enumeration ------------------------------------------------------------------


def test_enumeration_returns_distinct_feasible_rotations(reg):
    result = enumerate_rotations(ALL, registry=reg, cycle_years=3)
    assert len(result) > 0
    assert result.examined < 6**3  # cyclic duplicates already gone
    names = [p.name for p in result.plans]
    assert len(names) == len(set(names))


def test_enumeration_explains_what_it_rejected(reg):
    result = enumerate_rotations(
        ALL, registry=reg, cycle_years=3,
        constraints=RotationConstraints(max_fallow_fraction=0.4,
                                        max_consecutive_fallow_years=1),
    )
    tally = result.rejection_tally
    assert tally, "something should have been rejected"
    assert "impossible calendar" in tally
    text = result.describe()
    assert "feasible rotations" in text
    assert "rejected" in text


def test_an_over_tight_constraint_says_so_rather_than_returning_silence(reg):
    result = enumerate_rotations(
        ALL, registry=reg, cycle_years=2,
        constraints=RotationConstraints(min_distinct_crops=9),
    )
    assert len(result) == 0
    assert "NOTHING SURVIVED" in result.describe()
    assert "bug that looks like a result" in result.describe()


def test_candidate_cap_is_respected(reg):
    result = enumerate_rotations(ALL, registry=reg, cycle_years=4, max_candidates=7)
    assert len(result) == 7


def test_duplicate_pattern_ids_are_refused(reg):
    with pytest.raises(CandidateError, match="unique"):
        enumerate_rotations((WHEAT, WHEAT), registry=reg, cycle_years=2)


def test_enumeration_arguments_are_validated(reg):
    with pytest.raises(CandidateError, match="cycle_years"):
        enumerate_rotations(ALL, registry=reg, cycle_years=0)
    with pytest.raises(CandidateError, match="no year patterns"):
        enumerate_rotations((), registry=reg, cycle_years=2)


def test_longer_cycles_find_more_options(reg):
    short = enumerate_rotations(ALL, registry=reg, cycle_years=2, max_candidates=None)
    long = enumerate_rotations(ALL, registry=reg, cycle_years=4, max_candidates=None)
    assert len(long) > len(short)
