"""SCS curve number runoff.

The decision-relevant claim this module has to support is that ground cover reduces
runoff, and by how much. Tests therefore pin the ordering between cover types and
soil groups, not only the arithmetic.
"""

import numpy as np
import pytest

from rdss.agro.runoff import (
    CN2_TABLE,
    cn_dry,
    cn_for_wetness,
    cn_wet,
    condition_for_crop,
    cover_type_for_crop,
    curve_number,
    infiltration_mm,
    potential_retention_mm,
    scs_runoff_mm,
)


def test_curve_number_lookup_matches_tr55():
    assert curve_number("row_crop", "B", "good") == 78.0
    assert curve_number("row_crop", "B", "poor") == 81.0
    assert curve_number("fallow_bare", "D") == 94.0
    assert curve_number("meadow", "A", "good") == 30.0


def test_curve_number_rises_with_hydrologic_group():
    """A to D is increasing runoff potential, by definition."""
    for (cover, condition) in CN2_TABLE:
        cns = [curve_number(cover, g, condition) for g in "ABCD"]
        assert cns == sorted(cns), (cover, condition, cns)


def test_bare_fallow_has_the_highest_curve_number_on_every_soil():
    for group in "ABCD":
        bare = curve_number("fallow_bare", group)
        others = [
            curve_number(c, group, cond)
            for (c, cond) in CN2_TABLE
            if c != "fallow_bare"
        ]
        assert bare >= max(others), group


def test_ground_cover_lowers_the_curve_number():
    """The quantitative basis for 'a cover crop reduces runoff'."""
    for group in "ABCD":
        assert curve_number("meadow", group, "good") < curve_number(
            "close_seeded_legume", group, "good"
        ) < curve_number("row_crop", group, "good") <= curve_number(
            "fallow_bare", group
        )


def test_good_condition_never_exceeds_poor():
    for cover in {c for c, _ in CN2_TABLE}:
        for group in "ABCD":
            assert curve_number(cover, group, "good") <= curve_number(
                cover, group, "poor"
            )


def test_curve_number_rejects_bad_arguments():
    with pytest.raises(ValueError, match="condition"):
        curve_number("row_crop", "B", "excellent")
    with pytest.raises(ValueError, match="hydrologic group"):
        curve_number("row_crop", "E")
    with pytest.raises(ValueError, match="cover type"):
        curve_number("vineyard", "B")


# --- retention and the runoff equation ------------------------------------------


def test_potential_retention_is_the_millimetre_form():
    assert potential_retention_mm(100.0) == pytest.approx(0.0)
    assert potential_retention_mm(50.0) == pytest.approx(254.0)
    assert potential_retention_mm(80.0) == pytest.approx(63.5)


def test_retention_falls_as_curve_number_rises():
    cns = np.array([40.0, 60.0, 80.0, 95.0])
    s = potential_retention_mm(cns)
    assert np.all(np.diff(s) < 0)


def test_no_runoff_until_initial_abstraction_is_satisfied():
    cn = 80.0
    ia = 0.2 * potential_retention_mm(cn)
    assert scs_runoff_mm(ia * 0.99, cn) == pytest.approx(0.0)
    assert scs_runoff_mm(ia * 1.5, cn) > 0.0


def test_runoff_never_exceeds_rainfall_and_is_monotonic():
    rain = np.linspace(0.0, 300.0, 61)
    for cn in (40.0, 70.0, 94.0):
        ro = scs_runoff_mm(rain, cn)
        assert np.all(ro <= rain + 1e-9)
        assert np.all(ro >= 0.0)
        assert np.all(np.diff(ro) >= -1e-9)


def test_runoff_fraction_rises_with_storm_size():
    """Small storms infiltrate; large storms run off. This is the whole point."""
    cn = 85.0
    small = scs_runoff_mm(10.0, cn) / 10.0
    large = scs_runoff_mm(150.0, cn) / 150.0
    assert small < 0.25 < large


def test_the_same_storm_runs_off_more_on_a_heavier_soil():
    storm = 60.0
    ro = [scs_runoff_mm(storm, curve_number("row_crop", g, "good")) for g in "ABCD"]
    assert ro == sorted(ro)
    assert ro[-1] > ro[0] * 1.5


def test_cover_crop_versus_bare_soil_on_the_same_storm():
    """Quantifies the benefit the tool will claim for closing a bare window."""
    storm = 50.0
    bare = scs_runoff_mm(storm, curve_number("fallow_bare", "C"))
    covered = scs_runoff_mm(storm, curve_number("close_seeded_legume", "C", "good"))
    assert covered < bare
    assert (bare - covered) / bare > 0.25


def test_smaller_initial_abstraction_ratio_yields_more_runoff():
    """The 0.2 convention is contested; the sensitivity must be inspectable."""
    rain = 15.0
    cn = 80.0
    assert scs_runoff_mm(rain, cn, 0.05) > scs_runoff_mm(rain, cn, 0.2)


def test_infiltration_complements_runoff():
    rain = np.array([0.0, 5.0, 40.0, 120.0])
    cn = 82.0
    assert np.allclose(infiltration_mm(rain, cn) + scs_runoff_mm(rain, cn), rain)
    assert np.all(infiltration_mm(rain, cn) >= 0.0)


def test_zero_and_negative_rainfall_produce_no_runoff():
    assert scs_runoff_mm(0.0, 90.0) == pytest.approx(0.0)
    assert scs_runoff_mm(-5.0, 90.0) == pytest.approx(0.0)


# --- antecedent moisture adjustment ---------------------------------------------


def test_dry_and_wet_limits_bracket_cn2():
    for cn2 in (40.0, 60.0, 80.0, 94.0):
        assert cn_dry(cn2) < cn2 < cn_wet(cn2)
        assert cn_wet(cn2) <= 99.0


def test_wetness_interpolation_hits_the_three_anchor_points():
    cn2 = 78.0
    assert cn_for_wetness(cn2, 0.0) == pytest.approx(cn_wet(cn2))
    assert cn_for_wetness(cn2, 0.5) == pytest.approx(cn2)
    assert cn_for_wetness(cn2, 1.0) == pytest.approx(cn_dry(cn2))


def test_wetness_interpolation_is_monotonic():
    rd = np.linspace(0.0, 1.0, 21)
    cn = cn_for_wetness(78.0, rd)
    assert np.all(np.diff(cn) <= 1e-9)


def test_wetness_adjustment_is_clamped_outside_zero_to_one():
    cn2 = 78.0
    assert cn_for_wetness(cn2, -0.5) == pytest.approx(cn_for_wetness(cn2, 0.0))
    assert cn_for_wetness(cn2, 2.0) == pytest.approx(cn_for_wetness(cn2, 1.0))


def test_a_storm_on_a_wet_profile_runs_off_far_more():
    """A fixed CN would make the first storm after a dry spell behave identically."""
    storm = 40.0
    cn2 = curve_number("row_crop", "C", "good")
    on_dry = scs_runoff_mm(storm, cn_for_wetness(cn2, 0.9))
    on_wet = scs_runoff_mm(storm, cn_for_wetness(cn2, 0.05))
    assert on_wet > on_dry * 1.5


# --- crop mapping ----------------------------------------------------------------


def test_crops_map_to_sensible_cover_types(reg):
    assert cover_type_for_crop(reg.crop("wheat")) == "small_grain"
    assert cover_type_for_crop(reg.crop("rice_lowland")) == "small_grain"
    assert cover_type_for_crop(reg.crop("maize")) == "row_crop"
    assert cover_type_for_crop(reg.crop("cotton")) == "row_crop"
    assert cover_type_for_crop(reg.crop("potato")) == "row_crop"
    assert cover_type_for_crop(reg.crop("chickpea")) == "close_seeded_legume"
    assert cover_type_for_crop(reg.crop("sunn_hemp")) == "close_seeded_legume"
    assert cover_type_for_crop(reg.crop("alfalfa")) == "meadow"
    assert cover_type_for_crop(reg.crop("fallow_bare")) == "fallow_bare"


def test_wide_spaced_cereals_are_treated_as_row_crops(reg):
    """Drilled wheat and wide-row maize do not shed water alike."""
    assert cover_type_for_crop(reg.crop("sorghum")) == "row_crop"
    assert cover_type_for_crop(reg.crop("sugarcane")) == "row_crop"
    assert cover_type_for_crop(reg.crop("barley")) == "small_grain"


def test_every_crop_in_the_table_has_a_cover_type(reg):
    for crop in reg.crops.values():
        cover = cover_type_for_crop(crop)
        assert (cover, condition_for_crop(crop)) in CN2_TABLE, crop.id


def test_condition_follows_canopy_cover(reg):
    assert condition_for_crop(reg.crop("rice_lowland")) == "good"  # 0.95 cover
    assert condition_for_crop(reg.crop("lentil")) == "poor"  # 0.70 cover


def test_runoff_ordering_across_real_crops(reg):
    """End-to-end: the model must rank real rotation options correctly."""
    storm = 55.0

    def ro(crop_id):
        crop = reg.crop(crop_id)
        cn = curve_number(
            cover_type_for_crop(crop), "C", condition_for_crop(crop)
        )
        return float(scs_runoff_mm(storm, cn))

    assert ro("fallow_bare") > ro("potato") > ro("sunn_hemp") > ro("alfalfa")
