"""Guardrails: refusing recommendations the optimiser would happily make.

The only meaningful test of a crop screen is whether it agrees with what farmers
actually grow. Two false refusals were caught that way and both were instructive.

Winter wheat was refused in Finney County, Kansas - where it has been the dominant
crop for a century - because heat above the crop's maximum temperature was treated
as disqualifying. It is not: `t_max_c` is where growth ceases, not where the crop
dies, so heat reduces yield rather than making a crop impossible. Heat is now never
a refusal.

Winter wheat was also refused in Nebraska because a whole-season degree-day sum
counts dormant winter months at zero and so understates what an overwintering crop
achieves.

One disagreement remains and is deliberately left: grain sorghum is refused at the
cooler Nebraska site although it is grown there, using short-season hybrids needing
roughly 1300-1500 degree days against the 1700 in our table. The refusal is correct
for the cultivar it was given, so the verdict says so rather than the threshold
being loosened until it agrees.
"""

import json
import pathlib

import numpy as np
import pytest

from fieldshift.agro.climate import Site
from fieldshift.decide.guardrails import (
    CULTIVAR_CAVEAT,
    MIN_MATURITY_FREQUENCY,
    GuardrailError,
    Level,
    check_crop,
    frost_risk,
    heat_risk,
    market_and_seed_pathway,
    screen_crops,
    thermal_maturity,
    water_budget,
    waterlogging_risk,
)
from fieldshift.io.power import parse_power_daily

DATA = pathlib.Path(__file__).resolve().parents[1] / "data" / "raw"
SITES = {
    "ks_finney": Site(38.03, -100.74, 858.0),
    "ne_holt": Site(42.45, -98.78, 610.0),
    "tx_hale": Site(34.07, -101.82, 1030.0),
}
WINDOWS = {
    "wheat_winter": (274, 285), "maize": (125, 150), "sorghum": (152, 125),
    "rice_lowland": (152, 150), "sugarcane": (100, 360), "sesame": (152, 100),
}

pytestmark = pytest.mark.skipif(
    not all((DATA / f"power_{k}.json").exists() for k in SITES),
    reason="multi-site weather cache not present",
)


@pytest.fixture(scope="module")
def weather():
    out = {}
    for key, site in SITES.items():
        payload = json.loads((DATA / f"power_{key}.json").read_text())
        out[key], _ = parse_power_daily(payload, site=site)
    return out


# --- agreement with reality ------------------------------------------------------

GROWN = {
    ("ks_finney", "wheat_winter"): True,
    ("ks_finney", "maize"): True,
    ("ks_finney", "sorghum"): True,
    ("ks_finney", "rice_lowland"): False,
    ("ks_finney", "sugarcane"): False,
    ("ne_holt", "wheat_winter"): True,
    ("ne_holt", "maize"): True,
    ("ne_holt", "rice_lowland"): False,
    ("ne_holt", "sugarcane"): False,
    ("tx_hale", "wheat_winter"): True,
    ("tx_hale", "sorghum"): True,
    ("tx_hale", "rice_lowland"): False,
}


@pytest.mark.parametrize("key,crop_id", list(GROWN))
def test_the_screen_agrees_with_what_is_actually_grown(reg, weather, key, crop_id):
    doy, duration = WINDOWS[crop_id]
    report = check_crop(reg.crop(crop_id), weather[key], doy, duration)
    assert (not report.refused) == GROWN[(key, crop_id)], report.describe()


def test_the_regions_dominant_crop_is_never_refused(reg, weather):
    """The failure that exposed the heat bug: wheat is what Finney County grows."""
    report = check_crop(reg.crop("wheat_winter"), weather["ks_finney"], 274, 285)
    assert not report.refused
    assert report.warnings, "it should still warn about the heat it does experience"


def test_a_tropical_crop_is_refused_on_the_high_plains(reg, weather):
    for crop_id in ("rice_lowland", "sugarcane"):
        report = check_crop(reg.crop(crop_id), weather["ks_finney"], *WINDOWS[crop_id])
        assert report.refused
        assert any("maturity" in v.rule or "frost" in v.rule for v in report.refusals)


# --- thermal maturity -------------------------------------------------------------


def test_maturity_refusal_is_reserved_for_genuine_impossibility(reg, weather):
    assert MIN_MATURITY_FREQUENCY <= 0.25
    rice = thermal_maturity(reg.crop("rice_lowland"), weather["ks_finney"], 152, 150)
    assert rice.level is Level.REFUSE
    assert rice.frequency is not None and rice.frequency > 0.9


def test_a_maturity_refusal_names_the_cultivar_assumption(reg, weather):
    """Our table holds one cultivar per crop, so a refusal is about that cultivar."""
    sorghum = thermal_maturity(reg.crop("sorghum"), weather["ne_holt"], 152, 125)
    assert sorghum.level is Level.REFUSE
    assert "shorter-season hybrid" in sorghum.message
    assert sorghum.needs_local_review
    assert CULTIVAR_CAVEAT.strip(". ") in sorghum.message


def test_overwintering_crops_carry_a_dormancy_caveat(reg, weather):
    verdict = thermal_maturity(reg.crop("wheat_winter"), weather["ne_holt"], 274, 285)
    if verdict.level is not Level.PASS:
        assert "dormant" in verdict.message


def test_maturity_reports_the_years_it_failed(reg, weather):
    verdict = thermal_maturity(reg.crop("sorghum"), weather["ks_finney"], 152, 125)
    if verdict.years_affected:
        assert all(1984 <= y <= 2024 for y in verdict.years_affected)
        assert verdict.frequency == pytest.approx(
            len(verdict.years_affected) / 41, abs=0.05
        )


# --- frost -----------------------------------------------------------------------


def test_frost_is_judged_on_the_reproductive_window_only(reg, weather):
    """Winter frost on dormant wheat is normal; frost at anthesis is fatal.

    Testing the whole season would be simultaneously too strict for an
    overwintering crop and too lenient for a spring one.
    """
    wheat = frost_risk(reg.crop("wheat_winter"), weather["ne_holt"], 274, 285)
    assert wheat.level is not Level.REFUSE, (
        "winter wheat must not be refused for frost it sleeps through"
    )


def test_a_frost_sensitive_tropical_crop_is_refused(reg, weather):
    verdict = frost_risk(reg.crop("sugarcane"), weather["ne_holt"], 100, 360)
    assert verdict.level is Level.REFUSE
    assert verdict.frequency == pytest.approx(1.0, abs=0.05)


# --- heat: never a refusal -------------------------------------------------------


@pytest.mark.parametrize("key", list(SITES))
@pytest.mark.parametrize("crop_id", ["wheat_winter", "maize", "sorghum"])
def test_heat_is_never_a_refusal(reg, weather, key, crop_id):
    """t_max is where growth stops, not where the crop dies."""
    doy, duration = WINDOWS[crop_id]
    verdict = heat_risk(reg.crop(crop_id), weather[key], doy, duration)
    assert verdict.level is not Level.REFUSE


def test_frequent_heat_warns_and_asks_for_local_judgement(reg, weather):
    verdict = heat_risk(reg.crop("wheat_winter"), weather["ks_finney"], 274, 285)
    assert verdict.level is Level.WARN
    assert "accept knowingly" in verdict.message
    assert verdict.needs_local_review


# --- waterlogging, water budget, market ------------------------------------------


def test_waterlogging_only_concerns_intolerant_crops(reg, weather):
    tolerant = waterlogging_risk(reg.crop("rice_lowland"), weather["ks_finney"], 152, 150)
    assert tolerant.level is Level.PASS
    assert "not a limiting concern" in tolerant.message
    sensitive = waterlogging_risk(reg.crop("sesame"), weather["ks_finney"], 152, 100)
    assert sensitive.level in (Level.PASS, Level.WARN)


def test_water_budget_refuses_only_against_a_supplied_cap():
    assert water_budget(0.0, None).level is Level.PASS
    unknown = water_budget(500.0, None)
    assert unknown.level is Level.WARN
    assert unknown.needs_local_review
    assert water_budget(500.0, 300.0).level is Level.REFUSE
    ok = water_budget(200.0, 300.0)
    assert ok.level is Level.PASS
    assert "spare" in ok.message


def test_the_missing_groundwater_record_is_acknowledged():
    """We do not invent the cap, because it would silently decide viability."""
    import inspect
    from fieldshift.decide import guardrails
    source = inspect.getsource(guardrails.water_budget)
    assert "retired" in source
    assert "inventing" in source


def test_market_pathway_warns_but_never_refuses(reg):
    marketed = market_and_seed_pathway(reg.crop("wheat_winter"))
    assert marketed.level is Level.WARN
    assert marketed.needs_local_review
    assert "no nearby market" in marketed.message
    cover = market_and_seed_pathway(reg.crop("sunn_hemp"))
    assert cover.level is Level.PASS
    assert "soil-side" in cover.message


# --- assembly ---------------------------------------------------------------------


def test_every_crop_gets_every_rule(reg, weather):
    report = check_crop(reg.crop("maize"), weather["ks_finney"], 125, 150)
    rules = {v.rule for v in report.verdicts}
    assert rules == {
        "thermal maturity", "frost at flowering", "heat at flowering",
        "waterlogging", "water budget", "market pathway",
    }


def test_fallow_is_not_screened(reg, weather):
    report = check_crop(reg.crop("fallow_bare"), weather["ks_finney"], 274, 150)
    assert not report.refused
    assert report.verdicts[0].rule == "fallow"


def test_a_crop_with_no_duration_is_an_error(reg, weather):
    import dataclasses
    crop = dataclasses.replace(reg.crop("maize"), duration_days=0)
    with pytest.raises(GuardrailError, match="no duration"):
        check_crop(crop, weather["ks_finney"], 125)


def test_the_report_renders_readably(reg, weather):
    report = check_crop(reg.crop("rice_lowland"), weather["ks_finney"], 152, 150)
    text = report.describe()
    assert "REFUSED" in text
    assert "REFUSE" in text
    assert "growing degree days" in text


def test_screening_a_set_of_crops(reg, weather):
    crops = [reg.crop(c) for c in WINDOWS]
    reports = screen_crops(crops, weather["ks_finney"], WINDOWS)
    assert set(reports) == set(WINDOWS)
    assert any(r.refused for r in reports.values())
    assert any(not r.refused for r in reports.values())


def test_warnings_outnumber_refusals_across_the_whole_screen(reg, weather):
    """A guardrail layer that refuses freely removes options farmers may want."""
    warns = refuses = 0
    for key in SITES:
        for crop_id, (doy, duration) in WINDOWS.items():
            report = check_crop(reg.crop(crop_id), weather[key], doy, duration)
            warns += len(report.warnings)
            refuses += len(report.refusals)
    assert warns > refuses


# --- cover crops are not yield crops ---------------------------------------------


COVERS = [
    ("cereal_rye_cover", 288, 70),
    ("oats_cover", 240, 75),
    ("sorghum_sudan_cover", 180, 75),
    ("hairy_vetch", 265, 200),
    ("sunn_hemp", 180, 55),
]


@pytest.mark.parametrize("crop_id,doy,duration", COVERS)
def test_a_cover_crop_is_never_refused_for_not_maturing(reg, weather, crop_id, doy, duration):
    """A category error the screen originally made.

    Cereal rye sown for 70 autumn days was refused for "reaching maturity in 0% of
    years" - which is exactly how a rye cover is meant to be grown. A crop with no
    harvest is terminated on purpose, so a maturity requirement is the wrong test.
    """
    report = check_crop(reg.crop(crop_id), weather["ks_finney"], doy, duration)
    assert not report.refused, report.describe()


def test_a_cover_crop_reports_its_growth_window_instead(reg, weather):
    verdict = thermal_maturity(reg.crop("cereal_rye_cover"), weather["ks_finney"], 288, 70)
    assert verdict.rule == "growth window"
    assert verdict.level is Level.PASS
    assert "terminated before maturity by design" in verdict.message
    assert "degree days" in verdict.message


def test_too_short_a_cover_window_still_warns(reg, weather):
    """Not a refusal, but a cover sown too late builds little and is worth little."""
    verdict = thermal_maturity(reg.crop("sunn_hemp"), weather["ne_holt"], 288, 20)
    assert verdict.level in (Level.PASS, Level.WARN)
    if verdict.level is Level.WARN:
        assert "little biomass" in verdict.message
        assert verdict.needs_local_review


def test_frost_on_a_cover_crop_may_be_the_plan(reg, weather):
    """Oats sown as an autumn cover are grown precisely to be winter-killed."""
    verdict = frost_risk(reg.crop("oats_cover"), weather["ne_holt"], 240, 75)
    assert verdict.rule == "frost termination"
    assert verdict.level is Level.PASS
    assert "intended way to terminate" in verdict.message


def test_a_yield_crop_still_gets_the_yield_crop_tests(reg, weather):
    """The exemption must be narrow - it keys on having no marketable product."""
    assert reg.crop("cereal_rye_cover").economics.yield_typical == 0.0
    assert reg.crop("wheat_winter").economics.yield_typical > 0.0
    wheat = thermal_maturity(reg.crop("wheat_winter"), weather["ks_finney"], 274, 285)
    assert wheat.rule == "thermal maturity"
    frost = frost_risk(reg.crop("maize"), weather["ne_holt"], 125, 150)
    assert frost.rule == "frost at flowering"
