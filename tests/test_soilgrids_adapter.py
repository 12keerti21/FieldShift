"""SoilGrids adapter: units, refusals, and the provenance that must not be lost.

The adapter exists so the engine can leave the United States. These tests mostly
guard against the two ways a global soil source goes wrong quietly: a scaling
change that moves every number by a decade, and a prediction being presented as a
measurement.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fieldshift.data import load_registry
from fieldshift.io.soilgrids import (
    DEPTHS,
    SoilGridsError,
    _parse,
    fetch_soilgrids_point,
    texture_from_soilgrids,
)

CACHED = Path("data/raw/soilgrids_pb_sangrur.json")


def _payload(values: dict[str, list[int | None]]) -> dict:
    """Build a SoilGrids-shaped response from per-property depth values."""
    d_factors = {
        "clay": 10, "sand": 10, "silt": 10, "soc": 10, "bdod": 100,
        "wv0033": 10, "wv1500": 10, "cfvo": 10,
    }
    layers = []
    for name, series in values.items():
        depths = []
        for label, value in zip(DEPTHS, series):
            top, bottom = label.replace("cm", "").split("-")
            depths.append({
                "range": {"top_depth": int(top), "bottom_depth": int(bottom),
                          "unit_depth": "cm"},
                "label": label,
                "values": {"mean": value},
            })
        layers.append({
            "name": name,
            "unit_measure": {"d_factor": d_factors[name]},
            "depths": depths,
        })
    return {"properties": {"layers": layers}}


def _uniform(**overrides) -> dict:
    """A plausible six-layer loam profile."""
    base = {
        "clay": [220] * 6, "sand": [400] * 6, "silt": [380] * 6,
        "soc": [80] * 6, "bdod": [140] * 6,
        "wv0033": [300] * 6, "wv1500": [150] * 6, "cfvo": [0] * 6,
    }
    base.update(overrides)
    return _payload(base)


# ---------------------------------------------------------------- units


def test_units_are_converted_not_passed_through():
    profile = _parse(_uniform(), 30.0, 76.0)
    layer = profile.layers[0]
    assert layer.clay_pct == pytest.approx(22.0)
    assert layer.sand_pct == pytest.approx(40.0)
    assert layer.bulk_density_g_cm3 == pytest.approx(1.40)
    # soc 80 dg/kg -> 8 g/kg -> 0.8 percent
    assert layer.organic_carbon_pct == pytest.approx(0.8)
    # wv values are volumetric fractions after conversion
    assert layer.theta_fc == pytest.approx(0.30)
    assert layer.theta_wp == pytest.approx(0.15)


def test_available_water_is_the_retention_difference():
    profile = _parse(_uniform(), 30.0, 76.0)
    # (0.30 - 0.15) * 1000 = 150 mm per metre
    assert profile.awc_mm_per_m(150.0) == pytest.approx(150.0)


def test_coarse_fragments_reduce_available_water():
    """Stones hold no plant-available water, so a gravelly soil must store less."""
    stony = _parse(_uniform(cfvo=[250] * 6), 30.0, 76.0)
    clean = _parse(_uniform(cfvo=[0] * 6), 30.0, 76.0)
    assert stony.awc_mm_per_m(150.0) == pytest.approx(0.75 * clean.awc_mm_per_m(150.0))


def test_changed_scaling_upstream_is_refused_not_converted():
    """A d_factor we did not expect means every value is wrong by that ratio."""
    payload = _uniform()
    for layer in payload["properties"]["layers"]:
        if layer["name"] == "clay":
            layer["unit_measure"]["d_factor"] = 1
    with pytest.raises(SoilGridsError, match="changed the scaling"):
        _parse(payload, 30.0, 76.0)


# ---------------------------------------------------------------- refusals


def test_all_null_response_is_a_failure_not_an_empty_soil():
    """ISRIC answers HTTP 200 with nulls when throttling.

    Eight of ten test points did this on a first pass and succeeded on retry, so
    treating an all-null body as "no soil here" would report most of the world as
    unmapped.
    """
    with pytest.raises(SoilGridsError, match="no usable values"):
        _parse(_uniform(clay=[None] * 6, sand=[None] * 6, silt=[None] * 6,
                        soc=[None] * 6, bdod=[None] * 6, wv0033=[None] * 6,
                        wv1500=[None] * 6, cfvo=[None] * 6), 30.0, 76.0)


def test_missing_property_is_refused():
    payload = _uniform()
    payload["properties"]["layers"] = [
        L for L in payload["properties"]["layers"] if L["name"] != "wv1500"
    ]
    with pytest.raises(SoilGridsError, match="lacks"):
        _parse(payload, 30.0, 76.0)


def test_partial_depths_still_usable():
    """A prediction that stops at 30 cm is thin, not unusable."""
    profile = _parse(
        _uniform(clay=[220, 220, 220, None, None, None]), 30.0, 76.0
    )
    assert profile.described_depth_cm == 30.0


def test_implausible_water_retention_is_refused():
    """A unit error looks like an impossible soil, and must fail loudly."""
    registry = load_registry()
    # theta_fc below theta_wp gives zero available water
    payload = _uniform(wv0033=[100] * 6, wv1500=[400] * 6)
    with pytest.raises(SoilGridsError, match="plausible range"):
        texture_from_soilgrids(_parse(payload, 30.0, 76.0), registry.texture)


def test_texture_fractions_must_close():
    registry = load_registry()
    payload = _uniform(clay=[220] * 6, sand=[100] * 6, silt=[100] * 6)
    with pytest.raises(SoilGridsError, match="unit problem"):
        texture_from_soilgrids(_parse(payload, 30.0, 76.0), registry.texture)


# ---------------------------------------------------------------- provenance


def test_a_prediction_is_never_reported_as_measured():
    """The whole point of the two-tier design.

    SoilGrids does populate measured_awc_mm_per_m, because it is the best number
    available and it should override the texture centroid. But the farmer-facing
    flag must not call a 250 m model output a measurement.
    """
    registry = load_registry()
    soil = texture_from_soilgrids(_parse(_uniform(), 30.0, 76.0), registry.texture)
    assert soil.measured_awc_mm_per_m is not None
    assert soil.taw_mm_per_m == pytest.approx(150.0)
    assert soil.taw_provenance == "prediction"
    assert not soil.taw_is_measured
    assert not soil.measured_fractions
    assert soil.confidence == "medium"
    assert "prediction" in soil.source.lower() or "SoilGrids" in soil.source


def test_hydrologic_group_falls_back_to_the_texture_class():
    """SoilGrids has no hydrologic group, and the fallback must be visible.

    This is the main reason a SoilGrids site's runoff and erosion numbers carry
    lower confidence than a surveyed site's.
    """
    registry = load_registry()
    profile = _parse(_uniform(), 30.0, 76.0)
    soil = texture_from_soilgrids(profile, registry.texture)
    centroid = registry.texture(soil.id)
    assert soil.hydrologic_group == centroid.hydrologic_group
    assert "hydrologic group" in soil.source


# ---------------------------------------------------------------- the real point


@pytest.mark.skipif(not CACHED.exists(), reason="no cached Punjab response")
def test_the_cached_punjab_response_parses_and_is_plausible():
    registry = load_registry()
    profile = _parse(json.loads(CACHED.read_text()), 30.23, 76.09)
    soil = texture_from_soilgrids(profile, registry.texture)
    assert 60.0 <= soil.taw_mm_per_m <= 250.0
    assert 95.0 <= soil.sand_pct + soil.silt_pct + soil.clay_pct <= 105.0
    assert soil.organic_carbon_pct is not None and soil.organic_carbon_pct > 0.0
    assert soil.taw_provenance == "prediction"


@pytest.mark.skipif(not CACHED.exists(), reason="no cached Punjab response")
def test_cache_is_used_without_network():
    """A cached response must not hit the network, so builds are reproducible."""
    profile = fetch_soilgrids_point(
        30.23, 76.09, cache_path=str(CACHED), max_attempts=1
    )
    assert profile.layers
