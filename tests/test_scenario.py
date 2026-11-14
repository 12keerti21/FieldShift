"""Weather scenarios: a stress test that must not be mistaken for a projection.

The danger with this feature is not that the arithmetic is wrong - it is that a
uniform offset applied to an observed record gets presented as a climate
forecast. Several of these tests exist to keep that distinction load-bearing in
code rather than in a sentence someone can delete.
"""

from __future__ import annotations

import numpy as np
import pytest

from fieldshift.agro.climate import DailyWeather
from fieldshift.agro.et0 import et0_penman_monteith
from fieldshift.agro.climate import Site
from fieldshift.agro.scenario import (
    PRESETS,
    Scenario,
    ScenarioError,
    perturb,
)


def weather(n=400, rain_every=5, rain_mm=8.0, tmax=28.0, rh=55.0):
    start = np.datetime64("1990-01-01")
    dates = start + np.arange(n).astype("timedelta64[D]")
    precip = np.zeros(n)
    precip[::rain_every] = rain_mm
    return DailyWeather(
        dates=dates,
        tmax_c=np.full(n, tmax),
        tmin_c=np.full(n, tmax - 12.0),
        precip_mm=precip,
        rh_mean_pct=np.full(n, rh),
        wind_2m_ms=np.full(n, 2.0),
        solar_mj_m2=np.full(n, 20.0),
    )


# ------------------------------------------------------------- the guard rails


def test_a_scenario_outside_the_supported_range_is_refused():
    """Beyond a few degrees, replaying an observed record says nothing useful."""
    with pytest.raises(ScenarioError, match="outside"):
        Scenario(warming_c=12.0)
    with pytest.raises(ScenarioError, match="outside"):
        Scenario(rain_scale=0.0)


def test_every_scenario_carries_what_it_does_not_represent():
    """The disclaimer is data, not a sentence a caller must remember to print."""
    for sc in PRESETS:
        assert len(sc.caveats) >= 4
        joined = " ".join(sc.caveats).lower()
        assert "not a climate projection" in joined
        assert "variability" in joined
        assert "co2" in joined
        assert "planting dates" in joined


def test_the_baseline_is_the_untouched_record():
    w = weather()
    out = perturb(w, Scenario())
    assert out is w, "a baseline must not copy or alter the observed record"


def test_perturbing_never_mutates_the_original():
    """The baseline has to stay available, or every comparison is wrong."""
    w = weather()
    before_t = w.tmax_c.copy()
    before_p = w.precip_mm.copy()
    perturb(w, Scenario(warming_c=3.0, rain_scale=0.5))
    assert np.array_equal(w.tmax_c, before_t)
    assert np.array_equal(w.precip_mm, before_p)


# ------------------------------------------------------------- the physics


def test_warming_raises_both_temperatures_by_exactly_the_offset():
    w = weather()
    out = perturb(w, Scenario(warming_c=2.0))
    assert np.allclose(out.tmax_c, w.tmax_c + 2.0)
    assert np.allclose(out.tmin_c, w.tmin_c + 2.0)


def test_rainfall_is_scaled_so_dry_days_stay_dry():
    """Adding a constant would invent drizzle on days that had none.

    The number of wet days drives soil evaporation far more than the seasonal
    total does, so turning dry days wet would change the answer through a
    mechanism nobody asked for.
    """
    w = weather(rain_every=5)
    out = perturb(w, Scenario(rain_scale=0.5))
    dry_before = w.precip_mm == 0.0
    assert np.all(out.precip_mm[dry_before] == 0.0)
    assert np.allclose(out.precip_mm[~dry_before], w.precip_mm[~dry_before] * 0.5)
    assert int((out.precip_mm > 0).sum()) == int((w.precip_mm > 0).sum())


def test_warming_lowers_relative_humidity():
    """At constant moisture a warmer atmosphere holds proportionally less.

    Holding humidity fixed while raising temperature would understate the vapour
    pressure deficit, and therefore understate the extra crop water use - which
    is the main thing a farmer wants from this feature.
    """
    w = weather(rh=60.0)
    out = perturb(w, Scenario(warming_c=3.0))
    assert np.all(out.rh_mean_pct < w.rh_mean_pct)
    assert np.all(out.rh_mean_pct > 0.0)


def test_humidity_never_leaves_physical_bounds():
    w = weather(rh=4.0)
    out = perturb(w, Scenario(warming_c=6.0))
    assert np.all((out.rh_mean_pct >= 1.0) & (out.rh_mean_pct <= 100.0))


def test_a_hotter_scenario_is_also_a_thirstier_one():
    """The coupling that makes this worth doing at all.

    Reference ET is recomputed from the perturbed weather rather than adjusted
    by hand, so the extra evaporative demand of a warm year falls out of FAO-56
    instead of being asserted.
    """
    site = Site(38.0, -100.0, 850.0, "test")
    w = weather()
    base = et0_penman_monteith(site, w).values_mm.sum()
    hot = et0_penman_monteith(site, perturb(w, Scenario(warming_c=3.0))).values_mm.sum()
    assert hot > base * 1.02, "3 C of warming must raise evaporative demand"


def test_scenario_provenance_says_what_it_is():
    out = perturb(weather(), Scenario(warming_c=2.0, rain_scale=0.9))
    assert "scenario" in out.provenance
    assert "not a climate projection" in out.provenance["scenario_note"]


def test_a_scenario_names_itself_readably():
    assert Scenario(2.0, 0.9).name == "+2.0 C, rainfall x0.90"
    assert Scenario().name == "observed weather"
    assert Scenario(1.0, 1.0, "My label").name == "My label"
