"""Evidence cards: everything needed to audit one recommendation.

Two behaviours are the point of this layer and both are tested hard.

**Resolution mismatch is computed, not described.** "Coarse" conveys nothing. One
NASA POWER cell covers 270,400 hectares; a quarter-section field is 65. The ratio -
over four thousand to one - is what tells a farmer how far to trust an absolute
number, and it is calculated from the actual areas.

**Confidence comes from the weakest link, never an average.** A card resting on one
low-confidence input is a low-confidence card however many strong inputs sit beside
it. Averaging would let good inputs launder a bad one, which is the specific failure
this layer exists to prevent.
"""

import pytest

from fieldshift.decide.evidence import (
    CROP_TABLE,
    DEFAULT_FIELD_HA,
    GRIDMET,
    MODELS,
    NASADEM,
    POWER,
    QUICKSTATS,
    SSURGO,
    STANDING_UNKNOWNS,
    DataSource,
    EvidenceError,
    ModelBasis,
    build_card,
)
from fieldshift.decide.guardrails import Level, Verdict

REFUSAL = Verdict("thermal maturity", Level.REFUSE, "never ripens here", frequency=0.9)
WARNING = Verdict(
    "heat at flowering", Level.WARN, "hot during flowering", frequency=0.4,
    needs_local_review=True,
)
PASSING = Verdict("frost at flowering", Level.PASS, "no damaging frost")


# --- resolution mismatch ----------------------------------------------------------


def test_pixel_area_is_computed_from_the_native_resolution():
    assert POWER.pixel_hectares == pytest.approx(52_000.0**2 * 1e-4)
    assert GRIDMET.pixel_hectares == pytest.approx(1_600.0)
    assert NASADEM.pixel_hectares == pytest.approx(0.09)


def test_the_mismatch_ratio_is_how_many_fields_fit_in_one_pixel():
    assert POWER.mismatch_ratio(65.0) == pytest.approx(270_400.0 / 65.0, rel=1e-6)
    assert POWER.mismatch_ratio(65.0) > 4_000.0
    # NASADEM is finer than the field, so the ratio is below one.
    assert NASADEM.mismatch_ratio(65.0) < 1.0


def test_field_scale_sources_have_no_ratio():
    for source in (SSURGO, QUICKSTATS, CROP_TABLE):
        assert source.pixel_hectares is None
        assert source.mismatch_ratio(65.0) is None


def test_the_description_states_the_ratio_rather_than_calling_it_coarse():
    text = POWER.describe(65.0)
    assert "4,160x the field area" in text
    assert "coarse" not in text.lower()
    assert "270,400 ha pixel" in text


def test_a_finer_than_field_source_says_so():
    assert "finer than the field" in NASADEM.describe(65.0)


def test_the_coarsest_source_is_identified():
    card = build_card("x", sources=(GRIDMET, POWER, NASADEM))
    assert card.coarsest_source is POWER


def test_a_large_mismatch_triggers_an_explicit_warning_in_the_render():
    card = build_card("x", field_hectares=65.0, sources=(POWER, SSURGO))
    text = card.render()
    assert "4,160x the field area" in text
    assert "prefer the comparison between rotations" in text


def test_field_area_must_be_positive():
    with pytest.raises(EvidenceError, match="positive"):
        build_card("x", field_hectares=0.0)


def test_the_default_field_is_a_quarter_section():
    assert DEFAULT_FIELD_HA == pytest.approx(65.0, abs=1.0)


# --- confidence from the weakest link --------------------------------------------


def test_one_low_confidence_input_caps_the_whole_card():
    weak = DataSource("Guess", "x", "y", "z", None, "low")
    card = build_card("x", sources=(POWER, GRIDMET, SSURGO, weak))
    assert card.confidence == "low"
    assert weak in card.limiting_sources


def test_averaging_would_have_hidden_it():
    """Three high and one low averages to 'high-ish'; the floor is what matters."""
    weak = DataSource("Guess", "x", "y", "z", None, "low")
    card = build_card("x", sources=(POWER, GRIDMET, QUICKSTATS, weak))
    assert card.confidence != "high"
    assert card.confidence != "medium"


def test_all_sources_at_the_floor_are_named_not_just_one():
    """Naming one would imply fixing it alone would raise the card."""
    card = build_card("x", sources=(POWER, NASADEM, CROP_TABLE))
    assert card.confidence == "medium"
    names = {s.name for s in card.limiting_sources}
    assert names == {"NASADEM", "FieldShift crop table"}
    assert "improving these inputs" in card.render()


def test_a_card_with_only_strong_sources_is_high_confidence():
    card = build_card("x", sources=(POWER, GRIDMET, SSURGO, QUICKSTATS))
    assert card.confidence == "high"
    assert "capped by" not in card.render()


def test_a_card_with_no_sources_is_low_confidence():
    card = build_card("x", sources=())
    assert card.confidence == "low"
    assert card.limiting_sources == ()


def test_bad_confidence_labels_are_refused():
    with pytest.raises(EvidenceError, match="bad confidence"):
        DataSource("X", "v", "r", "p", None, "excellent")


# --- guardrails travel with the card ---------------------------------------------


def test_a_refusal_blocks_the_card_and_leads_the_render():
    card = build_card("Try rice here", guardrails=(REFUSAL, WARNING))
    assert card.is_blocked
    assert card.refusals == (REFUSAL,)
    text = card.render()
    assert "NOT RECOMMENDED HERE" in text
    assert text.index("NOT RECOMMENDED") < text.index("Risks")


def test_warnings_appear_with_their_frequencies():
    card = build_card("x", guardrails=(WARNING, PASSING))
    assert not card.is_blocked
    assert card.warnings == (WARNING,)
    assert "40% of years" in card.render()


def test_passing_guardrails_are_not_rendered_as_risks():
    card = build_card("x", guardrails=(PASSING,))
    assert card.warnings == ()
    assert "no damaging frost" not in card.render()


# --- what needs local review is assembled, not written ---------------------------


def test_review_items_are_gathered_from_guardrails_and_source_caveats():
    card = build_card("x", sources=(POWER, SSURGO, NASADEM), guardrails=(WARNING,))
    review = card.needs_local_review
    assert any("heat at flowering" in r for r in review)
    assert any("SSURGO" in r for r in review)
    assert any("NASADEM" in r for r in review)
    # POWER carries no caveat, so it must not appear.
    assert not any(r.startswith("NASA POWER") for r in review)


def test_review_cannot_drift_out_of_step_with_its_sources():
    """It is derived, so adding a caveated source adds a review item automatically."""
    few = build_card("x", sources=(POWER,), guardrails=())
    many = build_card("x", sources=(POWER, SSURGO, CROP_TABLE), guardrails=())
    assert len(many.needs_local_review) > len(few.needs_local_review)


# --- unknowns ---------------------------------------------------------------------


def test_standing_unknowns_are_always_present():
    card = build_card("x")
    for unknown in STANDING_UNKNOWNS:
        assert unknown in card.unknowns
    text = card.render()
    assert "wind erosion" in text
    assert "market access" in text
    assert "groundwater decline" in text


def test_extra_unknowns_are_added_not_substituted():
    card = build_card("x", extra_unknowns=("something specific to this field",))
    assert "something specific to this field" in card.unknowns
    assert len(card.unknowns) == len(STANDING_UNKNOWNS) + 1


def test_the_wind_erosion_gap_is_stated_on_every_card():
    """RUSLE reports the wrong mechanism at our site; a card must not hide that."""
    assert any("wind erosion" in u for u in STANDING_UNKNOWNS)


# --- models -----------------------------------------------------------------------


def test_every_model_carries_a_reference_and_a_role():
    for key, model in MODELS.items():
        assert model.reference, key
        assert model.role, key
        assert model.name, key


def test_validated_models_state_the_measured_result():
    assert "r = 0.966" in MODELS["et0"].validation
    assert "11 of 11" in MODELS["water"].validation
    assert "10.8%" in MODELS["rotation"].validation


def test_limitations_are_recorded_where_they_exist():
    assert "WATER erosion only" in MODELS["erosion"].limitation
    assert "structural" in MODELS["rotation"].limitation
    assert "top 23 cm" in MODELS["carbon"].limitation


def test_unknown_model_keys_are_refused():
    with pytest.raises(EvidenceError, match="unknown model keys"):
        build_card("x", model_keys=("telepathy",))


def test_a_model_renders_its_validation_and_limitation():
    text = MODELS["rotation"].describe()
    assert "validated" in text and "limitation" in text


# --- output shapes ----------------------------------------------------------------


def test_the_render_always_ends_with_the_standing_caveat():
    text = build_card("x").render()
    assert text.rstrip().endswith("replaces local agronomic judgement.")
    assert "supports a decision; it does not make one" in text


def test_numbers_are_rendered_in_the_order_given():
    card = build_card("x", numbers=(("first", "1"), ("second", "2")))
    text = card.render()
    assert text.index("first") < text.index("second")


def test_the_structured_form_carries_everything_the_render_does():
    card = build_card(
        "x", sources=(POWER, NASADEM), guardrails=(REFUSAL, WARNING),
        numbers=(("a", "1"),),
    )
    d = card.to_dict()
    assert d["confidence"] == card.confidence
    assert d["blocked"] is True
    assert d["numbers"] == {"a": "1"}
    assert d["limiting_sources"] == [s.name for s in card.limiting_sources]
    assert len(d["refusals"]) == 1 and len(d["warnings"]) == 1
    assert d["sources"][0]["mismatch_ratio"] > 4_000.0
    assert d["unknowns"] and d["needs_local_review"]


def test_the_structured_form_is_json_serialisable():
    import json
    json.dumps(build_card("x", sources=(POWER, SSURGO)).to_dict())
