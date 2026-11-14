"""SSURGO adapter, tested against a recorded Soil Data Access response.

The thing this adapter exists to fix: our parameter table ships USDA texture-class
centroids, and at the pilot point the centroid understated plant-available water by
52 percent. A measured survey value must always win over one derived from texture,
and these tests pin that precedence.
"""

import json
import pathlib

import pytest

from fieldshift.data import load_registry
from fieldshift.io.ssurgo import (
    DEFAULT_SURFACE_DEPTH_CM,
    SsurgoError,
    parse_sda_tables,
    texture_from_ssurgo,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ssurgo_finney_point.json"


@pytest.fixture
def profile():
    table = json.loads(FIXTURE.read_text())["Table"]
    return parse_sda_tables(table, 38.03, -100.74)


def test_the_dominant_component_is_parsed(profile):
    assert profile.component == "Richfield"
    assert profile.component_pct == pytest.approx(93.0)
    assert profile.hydrologic_group in "ABCD"
    assert len(profile.horizons) == 4


def test_horizons_are_ordered_and_contiguous(profile):
    tops = [h.top_cm for h in profile.horizons]
    assert tops == sorted(tops)
    for a, b in zip(profile.horizons, profile.horizons[1:]):
        assert a.bottom_cm == pytest.approx(b.top_cm)
    assert profile.horizons[-1].bottom_cm == pytest.approx(200.0)


def test_measured_available_water_far_exceeds_the_class_centroid(profile):
    """The whole reason this adapter exists."""
    awc = profile.awc_mm_per_m(150.0)
    assert 190.0 < awc < 230.0
    centroid = load_registry().texture("silt_loam").taw_mm_per_m
    assert awc > centroid * 1.4


def test_depth_weighting_respects_the_requested_depth(profile):
    shallow = profile.depth_weighted("clay_pct", 15.0)
    deep = profile.depth_weighted("clay_pct", 150.0)
    assert shallow < deep  # the subsoil at this site is markedly heavier
    assert shallow == pytest.approx(profile.surface_horizon().clay_pct, abs=1.0)


def test_organic_matter_is_converted_to_carbon_for_rothc(profile):
    """RothC needs carbon; SSURGO reports organic matter."""
    om = profile.depth_weighted("organic_matter_pct", 30.0)
    oc = profile.topsoil_organic_carbon_pct(30.0)
    assert oc == pytest.approx(om / 1.724, rel=1e-9)
    assert 0.3 < oc < 3.0


def test_requesting_a_depth_with_no_horizons_raises(profile):
    with pytest.raises(SsurgoError, match="no horizons"):
        profile.depth_weighted("clay_pct", 0.0)


# --- building a SoilTexture ------------------------------------------------------


def test_texture_is_classified_from_the_fao56_evaporation_layer(profile):
    """The class parameterises TEW and REW, so it must come from that same layer.

    At this site the top 16 cm is a silt loam; weighting past ~20 cm turns it into a
    silty clay loam because the next horizon reaches 39 percent clay. Picking the
    wrong depth silently changes the surface drying behaviour.
    """
    assert DEFAULT_SURFACE_DEPTH_CM <= 15.0
    reg = load_registry()
    soil = texture_from_ssurgo(profile, reg.texture)
    assert soil.id == "silt_loam"

    deep = texture_from_ssurgo(profile, reg.texture, surface_depth_cm=50.0)
    assert deep.id != "silt_loam"  # demonstrates the sensitivity


def test_measured_values_override_the_centroid(profile):
    reg = load_registry()
    soil = texture_from_ssurgo(profile, reg.texture)
    centroid = reg.texture(soil.id)

    assert soil.taw_is_measured
    assert not centroid.taw_is_measured
    assert soil.taw_mm_per_m == pytest.approx(profile.awc_mm_per_m(150.0))
    assert soil.taw_mm_per_m > centroid.taw_mm_per_m
    assert soil.measured_fractions
    assert soil.organic_carbon_pct is not None
    assert "SSURGO" in soil.source and "Richfield" in soil.source


def test_ssurgo_hydrologic_group_is_authoritative(profile):
    """NRCS assigns it from the limiting layer, not from texture."""
    reg = load_registry()
    soil = texture_from_ssurgo(profile, reg.texture)
    assert soil.hydrologic_group == profile.hydrologic_group


def test_conductivity_is_converted_from_micrometres_per_second(profile):
    reg = load_registry()
    soil = texture_from_ssurgo(profile, reg.texture)
    expected = profile.depth_weighted("ksat_um_s", DEFAULT_SURFACE_DEPTH_CM) * 3.6
    assert soil.ksat_mm_h == pytest.approx(expected)
    assert 0.1 < soil.ksat_mm_h < 300.0


def test_root_zone_storage_difference_is_material(profile):
    """The number that changes modelled stress and irrigation demand."""
    reg = load_registry()
    measured = texture_from_ssurgo(profile, reg.texture)
    assumed = reg.texture("silt_loam")
    assert measured.taw_mm(1.5) - assumed.taw_mm(1.5) > 80.0


# --- malformed responses ----------------------------------------------------------


def test_empty_table_is_rejected():
    with pytest.raises(SsurgoError, match="no horizon rows"):
        parse_sda_tables([["compname"]], 0.0, 0.0)


def test_component_without_usable_depths_is_rejected():
    table = [
        ["compname", "comppct_r", "hydgrp", "hzdept_r", "hzdepb_r"],
        ["Ghost", "100", "B", "10", "10"],
    ]
    with pytest.raises(SsurgoError, match="no usable horizon depths"):
        parse_sda_tables(table, 0.0, 0.0)


def test_missing_hydrologic_group_falls_back_without_crashing():
    table = [
        ["compname", "comppct_r", "hydgrp", "hzdept_r", "hzdepb_r", "awc_r"],
        ["Nameless", "100", None, "0", "50", "0.18"],
    ]
    p = parse_sda_tables(table, 0.0, 0.0)
    assert p.hydrologic_group == "B"
    assert p.awc_mm_per_m(50.0) == pytest.approx(180.0)
