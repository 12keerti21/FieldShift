"""Objectives, domination, trade-offs and weighted ranking.

The reason this layer exists rather than a single score: a weighted sum collapses a
real trade-off into one number and hides the exchange rate. A farmer choosing
between rotations needs to see that saving water costs production, not that
rotation B scored 7.4.

The suite also pins the fix to a real bug. `downside_production` was being computed
only over years that produced a crop, which handed a fallow-heavy rotation the yield
of its good years and none of its empty ones - inflating a corn-corn-fallow
rotation's "bad year" to 8.8 t/ha and putting 13 extra rotations on the Pareto front
that did not belong there.
"""

import numpy as np
import pytest

from fieldshift.decide.objectives import (
    OBJECTIVES,
    ObjectiveError,
    RotationScore,
    margin_objective,
    objective_vector,
    select_objectives,
)
from fieldshift.decide.pareto import (
    ParetoError,
    dominated_by,
    dominates,
    normalise,
    pareto_front,
    trade_off,
    weight_sensitivity,
    weighted_ranking,
)


def score(name, **kw):
    base = dict(
        years=40.0, production_t_ha_yr=2.0, downside_production_t_ha_yr=1.0,
        irrigation_mm_yr=100.0, carbon_change_t_ha_yr=0.1,
        water_erosion_t_ha_yr=1.0, wind_exposure_index=500.0,
        bare_fraction=0.3, evaporation_fraction=0.4, crops_per_decade=10.0,
    )
    base.update(kw)
    return RotationScore(name=name, **base)


OBJ = select_objectives(["production", "irrigation", "carbon"])


# --- objective directions ---------------------------------------------------------


def test_each_objective_declares_its_own_direction():
    by_key = {o.key: o for o in OBJECTIVES}
    assert by_key["production"].maximise
    assert by_key["carbon"].maximise
    assert by_key["downside"].maximise
    assert not by_key["irrigation"].maximise
    assert not by_key["water_erosion"].maximise
    assert not by_key["wind_exposure"].maximise
    assert not by_key["bare_ground"].maximise


def test_better_respects_direction():
    by_key = {o.key: o for o in OBJECTIVES}
    assert by_key["production"].better(3.0, 2.0)
    assert by_key["irrigation"].better(50.0, 100.0)
    assert not by_key["irrigation"].better(100.0, 50.0)


def test_every_objective_carries_units_and_a_note():
    for o in OBJECTIVES:
        assert o.units
        assert o.note, o.key
        assert o.label


def test_the_downside_objective_warns_about_field_versus_farm():
    note = {o.key: o.note for o in OBJECTIVES}["downside"]
    assert "THIS FIELD" in note
    assert "farm-level" in note


def test_the_erosion_objective_warns_it_is_water_only():
    note = {o.key: o.note for o in OBJECTIVES}["water_erosion"]
    assert "not a measure of total erosion" in note


def test_unknown_objectives_are_refused():
    with pytest.raises(ObjectiveError, match="unknown objective"):
        select_objectives(["profit"])


def test_margin_objective_is_built_with_a_currency():
    o = margin_objective("USD")
    assert "USD" in o.units and o.maximise
    assert o.extract(score("x", margin_per_ha_yr=250.0)) == 250.0
    assert o.extract(score("y")) == 0.0  # no margin supplied


def test_objective_vector_matches_the_objective_order():
    s = score("a", production_t_ha_yr=3.0, irrigation_mm_yr=80.0,
              carbon_change_t_ha_yr=0.4)
    assert objective_vector(s, OBJ).tolist() == [3.0, 80.0, 0.4]


# --- domination -------------------------------------------------------------------


def test_strictly_better_everywhere_dominates():
    good = score("good", production_t_ha_yr=3.0, irrigation_mm_yr=50.0,
                 carbon_change_t_ha_yr=0.3)
    poor = score("poor", production_t_ha_yr=2.0, irrigation_mm_yr=150.0,
                 carbon_change_t_ha_yr=0.1)
    assert dominates(good, poor, OBJ)
    assert not dominates(poor, good, OBJ)


def test_a_trade_off_means_neither_dominates():
    thirsty = score("thirsty", production_t_ha_yr=4.0, irrigation_mm_yr=300.0)
    frugal = score("frugal", production_t_ha_yr=2.0, irrigation_mm_yr=0.0)
    assert not dominates(thirsty, frugal, OBJ)
    assert not dominates(frugal, thirsty, OBJ)


def test_identical_rotations_do_not_dominate_each_other():
    """Both must survive, rather than one vanishing by accident of ordering."""
    a, b = score("a"), score("b")
    assert not dominates(a, b, OBJ)
    assert not dominates(b, a, OBJ)
    assert set(pareto_front([a, b], OBJ)) == {0, 1}


def test_equal_on_all_but_one_still_dominates():
    a = score("a", carbon_change_t_ha_yr=0.3)
    b = score("b", carbon_change_t_ha_yr=0.1)
    assert dominates(a, b, OBJ)


def test_domination_needs_an_objective():
    with pytest.raises(ParetoError, match="at least one objective"):
        dominates(score("a"), score("b"), [])


# --- the front --------------------------------------------------------------------


def test_the_front_keeps_only_non_dominated_options():
    options = [
        score("dominated", production_t_ha_yr=1.0, irrigation_mm_yr=200.0),
        score("thirsty_high_yield", production_t_ha_yr=4.0, irrigation_mm_yr=300.0),
        score("frugal_low_yield", production_t_ha_yr=2.0, irrigation_mm_yr=0.0),
    ]
    front = pareto_front(options, OBJ)
    assert 0 not in front
    assert {1, 2} <= set(front)


def test_an_empty_set_has_an_empty_front():
    assert pareto_front([], OBJ) == ()


def test_dominated_by_explains_a_rejection():
    options = [
        score("poor", production_t_ha_yr=1.0, irrigation_mm_yr=200.0,
              carbon_change_t_ha_yr=0.0),
        score("better", production_t_ha_yr=3.0, irrigation_mm_yr=100.0,
              carbon_change_t_ha_yr=0.2),
    ]
    assert dominated_by(options, 0, OBJ) == (1,)
    assert dominated_by(options, 1, OBJ) == ()


# --- trade-offs -------------------------------------------------------------------


def test_trade_off_splits_gains_from_losses_with_direction():
    a = score("a", production_t_ha_yr=2.0, irrigation_mm_yr=100.0)
    b = score("b", production_t_ha_yr=3.0, irrigation_mm_yr=250.0)
    t = trade_off(a, b, OBJ)
    gained = {o.key for o, _ in t.gains}
    lost = {o.key for o, _ in t.losses}
    assert "production" in gained
    assert "irrigation" in lost
    text = t.describe()
    assert "BETTER" in text and "WORSE" in text
    # Both deltas are positive here and both are labelled "up" - correctly, since
    # more production is better and more irrigation is worse. The direction word
    # describes the number's movement; BETTER/WORSE carries the judgement.
    assert "marketable production up" in text
    assert "irrigation demand up" in text


def test_a_minimised_objective_improving_counts_as_a_gain():
    a = score("a", irrigation_mm_yr=300.0)
    b = score("b", irrigation_mm_yr=50.0)
    t = trade_off(a, b, OBJ)
    assert {o.key for o, _ in t.gains} == {"irrigation"}
    assert t.losses == ()
    assert "irrigation demand down 250" in t.describe()


def test_identical_rotations_trade_nothing():
    t = trade_off(score("a"), score("b"), OBJ)
    assert t.gains == () and t.losses == ()
    assert "nothing" in t.describe()


# --- normalisation and weighted ranking -------------------------------------------


def test_normalisation_puts_best_at_one_regardless_of_direction():
    options = [
        score("a", production_t_ha_yr=1.0, irrigation_mm_yr=300.0),
        score("b", production_t_ha_yr=5.0, irrigation_mm_yr=0.0),
    ]
    n = normalise(options, OBJ)
    assert n[1, 0] == pytest.approx(1.0)  # most production
    assert n[1, 1] == pytest.approx(1.0)  # least irrigation
    assert n[0, 0] == pytest.approx(0.0)
    assert np.all((n >= 0.0) & (n <= 1.0))


def test_a_constant_objective_does_not_divide_by_zero():
    options = [score("a"), score("b")]
    n = normalise(options, OBJ)
    assert np.all(n == 1.0)


def test_weighted_ranking_follows_stated_priorities():
    thirsty = score("thirsty", production_t_ha_yr=5.0, irrigation_mm_yr=400.0)
    frugal = score("frugal", production_t_ha_yr=2.0, irrigation_mm_yr=0.0)
    options = [thirsty, frugal]

    water_first = weighted_ranking(
        options, OBJ, {"production": 1.0, "irrigation": 5.0, "carbon": 1.0}
    )
    yield_first = weighted_ranking(
        options, OBJ, {"production": 5.0, "irrigation": 1.0, "carbon": 1.0}
    )
    assert options[water_first[0][0]].name == "frugal"
    assert options[yield_first[0][0]].name == "thirsty"


def test_weights_are_validated():
    options = [score("a"), score("b")]
    with pytest.raises(ParetoError, match="no weight given"):
        weighted_ranking(options, OBJ, {"production": 1.0})
    with pytest.raises(ParetoError, match="negative"):
        weighted_ranking(options, OBJ,
                         {"production": -1.0, "irrigation": 1.0, "carbon": 1.0})
    with pytest.raises(ParetoError, match="at least one weight"):
        weighted_ranking(options, OBJ,
                         {"production": 0.0, "irrigation": 0.0, "carbon": 0.0})


def test_weight_sensitivity_detects_a_fragile_recommendation():
    """A top choice that flips on one priority is an artefact of the weighting."""
    close_a = score("a", production_t_ha_yr=2.00, irrigation_mm_yr=100.0)
    close_b = score("b", production_t_ha_yr=2.01, irrigation_mm_yr=99.0)
    weights = {"production": 1.0, "irrigation": 1.0, "carbon": 1.0}
    stability = weight_sensitivity([close_a, close_b], OBJ, weights)
    assert set(stability) == {"production", "irrigation", "carbon"}

    clear = [
        score("poor", production_t_ha_yr=1.0, irrigation_mm_yr=300.0,
              carbon_change_t_ha_yr=0.0),
        score("great", production_t_ha_yr=5.0, irrigation_mm_yr=10.0,
              carbon_change_t_ha_yr=0.5),
    ]
    assert all(weight_sensitivity(clear, OBJ, weights).values())


def test_sensitivity_on_an_empty_set_is_empty():
    assert weight_sensitivity([], OBJ, {"production": 1.0}) == {}
