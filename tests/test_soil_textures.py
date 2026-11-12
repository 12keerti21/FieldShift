"""Soil texture table and the USDA classifier.

The classifier matters more than it looks: gridded soil products give sand/silt/clay
fractions, not class names, so every field's hydraulic parameters come through this
function. A boundary error here silently mis-parameterises the whole water balance.
"""

import dataclasses

import pytest

from rdss.data import ParameterError, classify_texture
from rdss.data.registry import _validate


def test_all_twelve_usda_classes_present(reg):
    assert len(reg.textures) == 12
    assert {"sand", "loam", "silt_loam", "clay", "silty_clay_loam"} <= set(reg.textures)


def test_every_centroid_falls_in_its_own_class(reg):
    """If a centroid misclassifies, either it or the classifier is wrong."""
    for s in reg.textures.values():
        assert classify_texture(s.sand_pct, s.silt_pct, s.clay_pct) == s.id, s.id


def test_water_contents_are_physically_ordered(reg):
    for s in reg.textures.values():
        assert s.theta_wp < s.theta_fc < s.theta_sat, s.id
        assert 0 < s.theta_sat < 0.6, s.id


def test_available_water_spans_the_expected_range(reg):
    """Coarse soils hold little; medium soils hold most. FAO-56 Table 19 territory."""
    sand = reg.texture("sand")
    silt_loam = reg.texture("silt_loam")
    assert sand.taw_mm_per_m == min(s.taw_mm_per_m for s in reg.textures.values())
    assert sand.taw_mm_per_m < 100
    assert silt_loam.taw_mm_per_m > sand.taw_mm_per_m
    for s in reg.textures.values():
        assert 40 <= s.taw_mm_per_m <= 220, s.id


def test_clay_holds_more_water_than_it_can_give(reg):
    """A large store is not a usable store - much is held below wilting point."""
    clay, loam = reg.texture("clay"), reg.texture("loam")
    assert clay.theta_fc > loam.theta_fc
    assert clay.theta_wp > loam.theta_wp
    assert clay.taw_mm_per_m <= loam.taw_mm_per_m


def test_conductivity_falls_monotonically_with_clay(reg):
    ordered = sorted(reg.textures.values(), key=lambda s: s.clay_pct)
    assert ordered[0].ksat_mm_h > ordered[-1].ksat_mm_h
    assert reg.texture("sand").ksat_mm_h == max(
        s.ksat_mm_h for s in reg.textures.values()
    )


def test_hydrologic_groups_track_texture(reg):
    assert reg.texture("sand").hydrologic_group == "A"
    assert reg.texture("clay").hydrologic_group == "D"
    for s in reg.textures.values():
        if s.clay_pct >= 40:
            assert s.hydrologic_group == "D", s.id


def test_drainable_porosity_flags_waterlogging_risk(reg):
    assert reg.texture("sand").drainable_porosity > reg.texture("clay").drainable_porosity


def test_coarse_and_fine_flags(reg):
    assert reg.texture("sand").is_coarse and not reg.texture("sand").is_fine
    assert reg.texture("clay").is_fine and not reg.texture("clay").is_coarse
    assert not reg.texture("loam").is_coarse and not reg.texture("loam").is_fine


# --- derived FAO-56 quantities ---------------------------------------------------


def test_taw_and_raw_scale_with_root_depth_and_depletion(reg):
    loam = reg.texture("loam")
    assert loam.taw_mm(1.0) == pytest.approx(loam.taw_mm_per_m)
    assert loam.taw_mm(1.5) == pytest.approx(195.0)
    assert loam.raw_mm(1.5, 0.55) == pytest.approx(0.55 * 195.0)
    assert loam.raw_mm(1.5, 0.55) < loam.taw_mm(1.5)


def test_tew_uses_half_wilting_point(reg):
    """The surface layer dries past wilting point; that is why a bare field loses more."""
    loam = reg.texture("loam")
    expected = 1000.0 * (loam.theta_fc - 0.5 * loam.theta_wp) * 0.10
    assert loam.tew_mm(0.10) == pytest.approx(expected)
    assert loam.tew_mm(0.10) > 1000.0 * (loam.theta_fc - loam.theta_wp) * 0.10


def test_deeper_roots_reach_more_water_on_a_retentive_soil(reg):
    """Quantifies why a deep-rooted crop buys little on sand."""
    gain_loam = reg.texture("loam").taw_mm(2.0) - reg.texture("loam").taw_mm(1.0)
    gain_sand = reg.texture("sand").taw_mm(2.0) - reg.texture("sand").taw_mm(1.0)
    assert gain_loam > gain_sand * 1.5


# --- the classifier -------------------------------------------------------------


@pytest.mark.parametrize(
    "sand,silt,clay,expected",
    [
        (100, 0, 0, "sand"),
        (95, 3, 2, "sand"),
        (85, 10, 5, "loamy_sand"),
        (55, 30, 15, "sandy_loam"),
        (45, 40, 15, "loam"),
        (20, 60, 20, "silt_loam"),
        (0, 100, 0, "silt"),
        (60, 13, 27, "sandy_clay_loam"),
        (35, 33, 32, "clay_loam"),
        (10, 56, 34, "silty_clay_loam"),
        (50, 10, 40, "sandy_clay"),
        (10, 45, 45, "silty_clay"),
        (30, 30, 40, "clay"),
        (0, 0, 100, "clay"),
    ],
)
def test_classifier_known_points(sand, silt, clay, expected):
    assert classify_texture(sand, silt, clay) == expected


def test_classifier_normalises_rounding_artefacts():
    """Gridded soil products routinely sum to 99 or 101; that must not be rejected."""
    assert classify_texture(42, 40, 17) == classify_texture(42, 40, 18)
    assert classify_texture(21, 65, 15) == "silt_loam"


def test_classifier_rejects_fractions_that_cannot_be_rounding():
    for bad in [(10, 10, 10), (50, 50, 50), (0, 0, 0)]:
        with pytest.raises(ValueError):
            classify_texture(*bad)


def test_classifier_output_is_always_a_known_class(reg):
    """Sweep the triangle; every point must land in the table."""
    seen = set()
    for sand in range(0, 101, 2):
        for clay in range(0, 101 - sand, 2):
            silt = 100 - sand - clay
            tid = classify_texture(sand, silt, clay)
            assert tid in reg.textures, (sand, silt, clay, tid)
            seen.add(tid)
    assert seen == set(reg.textures), set(reg.textures) - seen


# --- measured fractions win over centroids --------------------------------------


def test_measured_fractions_are_kept_not_replaced(reg):
    """Clay percent drives RothC and RUSLE directly; a centroid must not overwrite it."""
    s = reg.texture_from_fractions(30, 33, 37)
    assert s.id == "clay_loam"
    assert s.clay_pct == pytest.approx(37.0)
    assert s.measured_fractions is True
    assert reg.texture("clay_loam").measured_fractions is False


def test_measured_fractions_are_normalised(reg):
    s = reg.texture_from_fractions(21, 65, 15)  # sums to 101
    assert s.sand_pct + s.silt_pct + s.clay_pct == pytest.approx(100.0)


def test_bad_fractions_raise_parameter_error(reg):
    with pytest.raises(ParameterError):
        reg.texture_from_fractions(10, 10, 10)


def test_unknown_texture_raises(reg):
    with pytest.raises(ParameterError):
        reg.texture("volcanic_ash")


def test_soil_provenance_and_gaps_recorded(reg):
    assert reg.soil_notes["provenance"]
    assert reg.soil_notes["known_gaps"]


# --- the validator must reject broken texture data ------------------------------


def _with_texture(reg, tid, **changes):
    s = dataclasses.replace(reg.texture(tid), **changes)
    return dataclasses.replace(reg, textures={**reg.textures, tid: s})


def test_validator_rejects_inverted_water_contents(reg):
    bad = _with_texture(reg, "loam", theta_fc=0.05)
    with pytest.raises(ParameterError, match="out of order"):
        _validate(bad)


def test_validator_rejects_centroid_outside_its_own_class(reg):
    bad = _with_texture(reg, "loam", sand_pct=95, silt_pct=3, clay_pct=2)
    with pytest.raises(ParameterError, match="classifies as"):
        _validate(bad)


def test_validator_rejects_bad_hydrologic_group(reg):
    bad = _with_texture(reg, "loam", hydrologic_group="E")
    with pytest.raises(ParameterError, match="hydrologic group"):
        _validate(bad)


def test_validator_rejects_out_of_range_rew(reg):
    bad = _with_texture(reg, "loam", rew_mm=40.0)
    with pytest.raises(ParameterError, match="REW"):
        _validate(bad)
