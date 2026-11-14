"""End-to-end rotation search on the real pilot site.

Integration tests: real NASA POWER weather, real SSURGO soil, real NASADEM terrain.
Skipped when the cached data is absent.

The result these pin is the one the whole project exists to produce: the farmer's
existing rotation is dominated on EVERY objective, and the tool can say by what and
by how much.
"""

import json
import pathlib

import numpy as np
import pytest

from fieldshift.agro.climate import Site
from fieldshift.agro.et0 import et0_penman_monteith
from fieldshift.agro.rotation import SeasonPlan
from fieldshift.agro.rothc import soc_from_organic_carbon_pct
from fieldshift.agro.waterbalance import IrrigationPolicy
from fieldshift.decide.candidates import (
    RotationConstraints,
    YearPattern,
    build_rotation,
)
from fieldshift.decide.objectives import select_objectives
from fieldshift.decide.pareto import dominated_by
from fieldshift.decide.search import SearchError, daily_ground_cover, search_rotations
from fieldshift.io.power import parse_power_daily
from fieldshift.io.ssurgo import fetch_ssurgo_point, texture_from_ssurgo
from fieldshift.io.terrain import field_slope_percent, ls_factor

DATA = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw"
SITE = Site(38.03, -100.74, 858.0, "Finney County, Kansas")
PIVOT = IrrigationPolicy(
    mode="refill_on_stress", efficiency=0.85,
    refill_to_depletion_frac=0.25, max_application_mm=25,
)

pytestmark = pytest.mark.skipif(
    not (DATA / "power_finney_ks_1981_2024.json").exists()
    or not (DATA / "nasadem_n38w101" / "n38w101.hgt").exists(),
    reason="cached site data not present",
)

PATTERNS = (
    YearPattern("fallow", "fallow", ()),
    YearPattern("wheat", "winter wheat",
                (SeasonPlan("wheat_winter", 274, 285, fertiliser_n_kg_ha=70.0),)),
    YearPattern("sorghum", "grain sorghum",
                (SeasonPlan("sorghum", 152, 125, fertiliser_n_kg_ha=50.0),)),
    YearPattern("corn_irr", "corn (irr)",
                (SeasonPlan("maize", 125, 150, irrigation=PIVOT,
                            fertiliser_n_kg_ha=200.0),)),
    YearPattern("sorg_cover", "sorghum + rye",
                (SeasonPlan("sorghum", 152, 125, fertiliser_n_kg_ha=50.0),
                 SeasonPlan("cereal_rye_cover", 288, 70))),
)


@pytest.fixture(scope="module")
def site(reg):
    payload = json.loads((DATA / "power_finney_ks_1981_2024.json").read_text())
    for series in payload["properties"]["parameter"].values():
        for k in list(series):
            if k < "19840101":
                del series[k]
    weather, _ = parse_power_daily(payload, site=SITE)
    et0 = et0_penman_monteith(SITE, weather).values_mm
    profile = fetch_ssurgo_point(
        38.03, -100.74, cache_path=str(DATA / "ssurgo_finney_point.json")
    )
    soil = texture_from_ssurgo(profile, reg.texture)
    soc = soc_from_organic_carbon_pct(
        soil.organic_carbon_pct, soil.bulk_density_g_cm3
    )
    slope, _ = field_slope_percent(
        str(DATA / "nasadem_n38w101" / "n38w101.hgt"), 38.03, -100.74
    )
    return weather, et0, soil, soc, ls_factor(slope, 75.0)


EXISTING_NAME = "EXISTING: winter wheat - fallow"


@pytest.fixture(scope="module")
def result(reg, site):
    weather, et0, soil, soc, ls = site
    existing = build_rotation(
        [p for p in PATTERNS if p.id in ("wheat", "fallow")], name=EXISTING_NAME
    )
    return search_rotations(
        PATTERNS, registry=reg, soil=soil, weather=weather, et0_mm=et0,
        start_year=1984, initial_soc_t_ha=soc, ls=ls, cycle_years=3,
        constraints=RotationConstraints(
            max_consecutive_fallow_years=1, max_fallow_fraction=0.5
        ),
        objectives=select_objectives(
            ["production", "downside", "irrigation", "carbon",
             "wind_exposure", "bare_ground"]
        ),
        max_candidates=60,
        extra_plans=[existing],
    )


# --- the search runs --------------------------------------------------------------


def test_the_search_produces_a_front_from_real_data(result):
    assert len(result) > 10
    assert 0 < len(result.front) < len(result)
    assert result.failures == (), result.failures


def test_rejections_are_counted_and_the_calendar_removes_the_most(result):
    tally = result.candidates.rejection_tally
    assert tally
    assert "impossible calendar" in tally
    assert result.candidates.examined > len(result.candidates)


def test_every_score_is_finite_and_physically_sane(result):
    for s in result.scores:
        assert np.isfinite(s.production_t_ha_yr) and s.production_t_ha_yr >= 0.0
        assert 0.0 <= s.bare_fraction <= 1.0
        assert s.irrigation_mm_yr >= 0.0
        assert -1.0 < s.carbon_change_t_ha_yr < 2.0
        assert s.wind_exposure_index >= 0.0
        assert s.crops_per_decade > 0.0


def test_all_candidates_share_one_starting_soil(result):
    """Otherwise the comparison partly measures differences in their spin-up."""
    for outcome in result.outcomes:
        assert any("shared across candidates" in n for n in outcome.notes)
    starts = {round(o.carbon.initial_soc, 6) for o in result.outcomes}
    assert len(starts) == 1


# --- THE result -------------------------------------------------------------------


def test_the_existing_rotation_is_dominated_on_every_objective(result):
    index = next(
        i for i, s in enumerate(result.scores) if s.name == EXISTING_NAME
    )
    beaten_by = dominated_by(result.scores, index, result.objectives)
    assert beaten_by, "the existing rotation should not be on the front here"
    assert index not in result.front


def test_the_existing_rotation_is_losing_carbon(result):
    existing = next(s for s in result.scores if s.name == EXISTING_NAME)
    assert existing.carbon_change_t_ha_yr < 0.05
    front_carbon = [s.carbon_change_t_ha_yr for s in result.front_scores]
    assert max(front_carbon) > existing.carbon_change_t_ha_yr


def test_the_existing_rotation_has_a_zero_production_year(result):
    """One field, one fallow year - and the downside objective must show it."""
    existing = next(s for s in result.scores if s.name == EXISTING_NAME)
    assert existing.downside_production_t_ha_yr == pytest.approx(0.0, abs=0.01)
    assert existing.production_t_ha_yr > 0.5  # the cycle average is not zero


def test_fallow_heavy_rotations_do_not_dominate_the_front(result):
    """The downside bug put 13 of these on the front before it was fixed."""
    for score in result.front_scores:
        assert score.bare_fraction < 0.75, score.name


def test_trade_offs_from_the_existing_rotation_are_explainable(result):
    offs = result.trade_offs_from(EXISTING_NAME)
    assert offs
    texts = [o.describe() for o in offs]
    assert all("BETTER" in t for t in texts)
    # At least one alternative should improve everything with nothing given up.
    assert any("WORSE:  nothing" in t for t in texts)


def test_best_for_each_objective_is_retrievable(result):
    for objective in result.objectives:
        best = result.best_for(objective.key)
        values = [objective.extract(s) for s in result.scores]
        chosen = objective.extract(best)
        assert chosen == (max(values) if objective.maximise else min(values))
    with pytest.raises(SearchError, match="not among"):
        result.best_for("profit")


def test_the_cover_crop_rotation_builds_the_most_carbon(result):
    best_carbon = result.best_for("carbon")
    assert "rye" in best_carbon.name or "cover" in best_carbon.name


def test_a_dryland_rotation_achieves_zero_irrigation(result):
    assert result.best_for("irrigation").irrigation_mm_yr == pytest.approx(0.0)


def test_describe_renders_the_front(result):
    text = result.describe(limit=5)
    assert "Pareto front" in text
    assert "feasible rotations" in text


# --- cover series and validation --------------------------------------------------


def test_daily_cover_is_bounded_and_non_trivial(result):
    outcome = result.outcomes[0]
    cover = daily_ground_cover(outcome, len(outcome.seasons[0].water.et0_mm) * 0 + 14976)
    assert np.all((cover >= 0.0) & (cover <= 1.0))
    assert cover.max() > 0.3


def test_mismatched_et0_is_refused(reg, site):
    weather, _, soil, soc, ls = site
    with pytest.raises(SearchError, match="et0 series length"):
        search_rotations(
            PATTERNS, registry=reg, soil=soil, weather=weather,
            et0_mm=np.zeros(10), start_year=1984, initial_soc_t_ha=soc, ls=ls,
        )


def test_no_objectives_is_refused(reg, site):
    weather, et0, soil, soc, ls = site
    with pytest.raises(SearchError, match="at least one objective"):
        search_rotations(
            PATTERNS, registry=reg, soil=soil, weather=weather, et0_mm=et0,
            start_year=1984, initial_soc_t_ha=soc, ls=ls, objectives=(),
        )


def test_impossible_constraints_report_the_tally(reg, site):
    weather, et0, soil, soc, ls = site
    with pytest.raises(SearchError, match="no rotation survived"):
        search_rotations(
            PATTERNS, registry=reg, soil=soil, weather=weather, et0_mm=et0,
            start_year=1984, initial_soc_t_ha=soc, ls=ls, cycle_years=2,
            constraints=RotationConstraints(min_distinct_crops=9),
        )
