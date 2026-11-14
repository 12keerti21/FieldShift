"""Structural and agronomic sanity checks on the crop parameter table.

Most structural rules are enforced at load time by registry._validate; these tests
pin the rules themselves so a future edit cannot quietly relax them, and assert a
handful of agronomic facts that must stay true for the engine to be trustworthy.
"""

import dataclasses

import pytest

from rdss.data import ParameterError
from rdss.data.registry import _validate


def test_table_loads_with_expected_shape(reg):
    assert len(reg.crops) >= 25
    assert reg.c_fraction_of_dry_matter == pytest.approx(0.45)


def test_every_crop_has_sources_and_confidence(reg):
    for c in reg.crops.values():
        assert c.meta.sources, c.id
        assert c.meta.confidence in {"high", "medium", "low"}, c.id


def test_stage_lengths_sum_to_duration(reg):
    for c in reg.crops.values():
        if c.is_fallow:
            continue
        assert c.stages_days.total == c.duration_days, c.id


def test_nitrogen_fixers_are_all_legumes(reg):
    fixers = [c for c in reg.crops.values() if c.nitrogen.is_fixer]
    assert len(fixers) >= 8
    for c in fixers:
        assert c.family == "Fabaceae", c.id
        assert c.nitrogen.bnf_kg_n_ha[1] > 0
        assert 0 < c.nitrogen.ndfa_fraction <= 1


def test_non_fixers_declare_zero_fixation(reg):
    for c in reg.crops.values():
        if not c.nitrogen.is_fixer:
            assert c.nitrogen.bnf_kg_n_ha == (0, 0), c.id
            assert c.nitrogen.ndfa_fraction == 0, c.id


def test_dpm_rpm_ratios_are_rothc_values(reg):
    for c in reg.crops.values():
        assert c.residue.dpm_rpm_ratio in {1.44, 0.67, 0.25}, c.id
    # Perennial forages behave like improved grassland, not arable.
    assert reg.crop("alfalfa").residue.dpm_rpm_ratio == 0.67
    assert reg.crop("wheat").residue.dpm_rpm_ratio == 1.44


def test_bare_fallow_is_a_true_zero_input_slot(reg):
    f = reg.crop("fallow_bare")
    assert f.is_fallow
    assert f.traits.max_canopy_cover_frac == 0.0
    assert not f.provides_ground_cover
    assert f.residue_dm_t_ha() == 0.0
    assert f.water.seasonal_req_mm == (0, 0)


def test_short_window_catch_crops_exist(reg):
    """The bare-window analysis is pointless without crops that can fill one."""
    short = [c for c in reg.crops.values() if 0 < c.duration_days <= 75]
    assert {"mungbean", "sunn_hemp", "sesbania", "sorghum_sudan_cover"} <= {c.id for c in short}


def test_cover_crops_cover_both_thermal_windows(reg):
    covers = [c for c in reg.crops.values() if c.traits.cover_crop]
    seasons = {s for c in covers for s in c.typical_seasons}
    assert "warm_season" in seasons and "cool_season" in seasons


def test_potato_is_the_most_irrigation_dependent_upland_crop(reg):
    """Shallow roots plus a low depletion fraction: it cannot ride out a dry spell.

    Lowland rice is excluded deliberately. Its p_depletion of 0.20 is the lowest in
    the table, but that encodes intentional ponding in a flooded system, not
    sensitivity to drying - the two must not be conflated when ranking irrigation
    dependence, and a flooded field also needs a percolation term the standard
    FAO-56 root-zone balance does not provide.
    """
    potato = reg.crop("potato")
    assert potato.root.max_depth_m <= 0.7

    upland = [
        c
        for c in reg.crops.values()
        if not c.is_fallow and c.traits.waterlogging_tolerance < 5
    ]
    assert "rice_lowland" not in {c.id for c in upland}
    assert potato.root.p_depletion == min(c.root.p_depletion for c in upland)


def test_deep_rooted_crops_actually_root_deep(reg):
    for c in reg.crops.values():
        if c.traits.deep_rooted:
            assert c.root.max_depth_m >= 1.2, c.id
    assert reg.crop("alfalfa").root.max_depth_m >= 3.0


def test_residue_mass_derives_from_yield(reg):
    wheat = reg.crop("wheat")
    assert wheat.residue_dm_t_ha(4.0) == pytest.approx(5.6)
    assert wheat.residue_dm_t_ha() == pytest.approx(5.6)  # typical yield


def test_green_manures_carry_no_market_yield(reg):
    for cid in ("sunn_hemp", "sesbania", "cereal_rye_cover", "sorghum_sudan_cover"):
        c = reg.crop(cid)
        assert c.economics.yield_t_ha == (0.0, 0.0, 0.0), cid
        assert c.economics.price_local_required is False, cid


def test_marketed_crops_all_require_local_prices(reg):
    """No fabricated prices anywhere in the table."""
    for c in reg.crops.values():
        if c.economics.yield_typical > 0:
            assert c.economics.price_local_required is True, c.id


def test_provenance_and_gaps_are_recorded(reg):
    """The honesty layer reads these; an empty list must fail the build."""
    assert reg.crop_notes["provenance"]
    assert reg.crop_notes["known_gaps"]
    assert reg.pathogen_notes["provenance"]
    assert reg.pathogen_notes["known_gaps"]


def test_unknown_crop_raises(reg):
    with pytest.raises(ParameterError):
        reg.crop("quinoa")


# --- the validator must actually reject bad data ---------------------------------


def _with_crop(reg, crop_id, **changes):
    crop = dataclasses.replace(reg.crop(crop_id), **changes)
    return dataclasses.replace(reg, crops={**reg.crops, crop_id: crop})


def test_validator_rejects_stage_sum_mismatch(reg):
    bad = _with_crop(reg, "wheat", duration_days=999)
    with pytest.raises(ParameterError, match="stage lengths sum"):
        _validate(bad)


def test_validator_rejects_non_legume_fixer(reg):
    nitro = dataclasses.replace(reg.crop("maize").nitrogen, is_fixer=True)
    bad = _with_crop(reg, "maize", nitrogen=nitro)
    with pytest.raises(ParameterError):
        _validate(bad)


def test_validator_rejects_implausible_kc(reg):
    bad = _with_crop(reg, "maize", kc=dataclasses.replace(reg.crop("maize").kc, mid=3.0))
    with pytest.raises(ParameterError, match="kc.mid"):
        _validate(bad)


def test_validator_rejects_bad_dpm_rpm(reg):
    res = dataclasses.replace(reg.crop("wheat").residue, dpm_rpm_ratio=1.0)
    bad = _with_crop(reg, "wheat", residue=res)
    with pytest.raises(ParameterError, match="not a RothC value"):
        _validate(bad)


def test_basal_coefficients_never_exceed_single_coefficients(reg):
    """kcb excludes soil evaporation, so kcb <= kc must hold for every crop.

    This is the guard that would have caught storing FAO-56 Table 12 single
    coefficients where Table 17 basal coefficients belong.
    """
    for c in reg.crops.values():
        assert c.kcb.ini <= c.kc.ini + 1e-9, c.id
        assert c.kcb.mid <= c.kc.mid + 1e-9, c.id
        assert c.kcb.end <= c.kc.end + 1e-9, c.id


def test_annual_crops_start_from_bare_soil_basal_transpiration(reg):
    """At emergence there is no canopy, so Kcb_ini is ~0.15 for annual crops."""
    for c in reg.crops.values():
        if c.is_fallow or c.multi_year or c.group == "forage":
            continue
        if c.id == "rice_lowland":
            continue  # flooded: the water surface itself transpires-equivalent
        assert c.kcb.ini == pytest.approx(0.15), c.id


def test_bare_fallow_has_zero_basal_transpiration(reg):
    f = reg.crop("fallow_bare")
    assert (f.kcb.ini, f.kcb.mid, f.kcb.end) == (0.0, 0.0, 0.0)
    assert f.kc.ini > 0.0  # but the single coefficient is non-zero: soil evaporates
