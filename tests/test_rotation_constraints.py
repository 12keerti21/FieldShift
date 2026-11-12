"""Pathogen table and return-interval constraint logic.

These tests exist to stop two specific failure modes that would discredit the tool:
claiming a disease benefit rotation cannot deliver, and passing a rotation that
actually replants a host too soon once the cycle repeats.
"""

import dataclasses

import pytest

from rdss.data import ParameterError
from rdss.data.registry import _validate


def test_pathogen_table_loads(reg):
    assert len(reg.pathogens) >= 15


def test_every_pathogen_resolves_at_least_one_host(reg):
    for p in reg.pathogens.values():
        assert reg.hosts(p), p.id


def test_family_level_host_ranges_expand(reg):
    """Clubroot lists only Brassicaceae; mustard must still be caught."""
    clubroot = reg.pathogen("plasmodiophora_brassicae")
    assert clubroot.host_crops == ()
    assert "mustard" in reg.hosts(clubroot)

    wilt = reg.pathogen("ralstonia_solanacearum")
    assert "potato" in reg.hosts(wilt)


def test_recommended_interval_never_exceeds_persistence(reg):
    for p in reg.pathogens.values():
        assert p.recommended_return_interval_years <= p.soil_persistence_years + 1, p.id


# --- rotation must not be credited where it does nothing -------------------------


def test_airborne_rust_is_excluded_from_intervals(reg):
    """Rusts blow in from outside the field; a cereal break cannot touch them."""
    rust = reg.pathogen("puccinia_rusts")
    assert rust.rotation_effective is False
    assert rust.soil_persistence_years == 0

    effective = {p.id for p in reg.pathogens_for("wheat")}
    assert "puccinia_rusts" not in effective

    everything = {p.id for p in reg.pathogens_for("wheat", rotation_effective_only=False)}
    assert "puccinia_rusts" in everything


def test_rice_blast_is_excluded_from_intervals(reg):
    assert reg.pathogen("magnaporthe_oryzae").rotation_effective is False
    assert "magnaporthe_oryzae" not in {p.id for p in reg.pathogens_for("rice_lowland")}


def test_broad_host_pathogens_are_marked_weak(reg):
    """Rotation against a 500-host organism is near-useless and must say so."""
    for pid in ("macrophomina_phaseolina", "sclerotium_rolfsii"):
        assert reg.pathogen(pid).rotation_efficacy == "weak", pid


def test_cereal_break_against_sclerotinia_is_strong_enough_to_claim(reg):
    """Cereals are true non-hosts, so here the tool may state a real benefit."""
    scl = reg.pathogen("sclerotinia_sclerotiorum")
    assert scl.rotation_effective
    assert scl.rotation_efficacy == "partial"
    assert "wheat" not in reg.hosts(scl)
    assert "mustard" in reg.hosts(scl)


# --- derived intervals ----------------------------------------------------------


@pytest.mark.parametrize(
    "crop_id,expected_years,expected_driver_fragment",
    [
        ("chickpea", 5, "Fusarium wilt"),
        ("mustard", 7, "Clubroot"),
        ("potato", 6, "cyst nematode"),
        ("wheat", 2, ""),  # a genuine tie, pinned separately below
        ("cotton", 4, "Verticillium"),
        ("alfalfa", 4, ""),
    ],
)
def test_known_return_intervals(reg, crop_id, expected_years, expected_driver_fragment):
    c = reg.min_return_interval(crop_id)
    assert c.years == expected_years, c
    assert expected_driver_fragment.lower() in c.driver.lower()
    assert c.explanation


def test_wheat_interval_is_driven_by_one_of_two_tied_cereal_pathogens(reg):
    """Cereal cyst nematode and take-all both ask for a 2-year break.

    Either is a correct answer, so the test fixes the set rather than the winner -
    but both are 'strong' efficacy, which is what lets the tool state plainly that a
    broadleaf break year works here.
    """
    c = reg.min_return_interval("wheat")
    assert c.years == 2
    assert c.driver in {"Cereal cyst nematode", "Take-all"}
    assert c.efficacy == "strong"


def test_interval_carries_efficacy_and_confidence(reg):
    """Downstream scoring needs to temper a constraint by how much rotation can do."""
    c = reg.min_return_interval("mungbean")
    assert c.efficacy in {"strong", "partial", "weak", "none"}
    assert c.confidence in {"high", "medium", "low"}


def test_allelopathy_imposes_an_interval_like_a_pathogen(reg):
    """Alfalfa autotoxicity is chemical, not infectious, but constrains identically."""
    auto = reg.pathogen("alfalfa_autotoxicity")
    assert auto.type == "allelopathy"
    assert "alfalfa" in reg.hosts(auto)
    assert reg.min_return_interval("alfalfa").years >= 2


def test_green_manures_are_unconstrained(reg):
    """Sunn hemp carries no recorded host constraint, which is why it fits anywhere."""
    assert reg.pathogens_for("sunn_hemp") == []
    assert reg.min_return_interval("sunn_hemp").years == 1


# --- shared pathogens: the real test of a disease break -------------------------


def test_two_cereals_share_pathogens(reg):
    shared = {p.id for p in reg.shared_pathogens("wheat", "barley")}
    assert {"heterodera_avenae", "gaeumannomyces_take_all"} <= shared


def test_distant_crops_share_nothing(reg):
    assert reg.shared_pathogens("wheat", "chickpea") == []


def test_shared_pathogens_is_symmetric(reg):
    a = reg.shared_pathogens("cotton", "potato")
    b = reg.shared_pathogens("potato", "cotton")
    assert [p.id for p in a] == [p.id for p in b]
    assert "verticillium_dahliae" in {p.id for p in a}


# --- rotation checking ----------------------------------------------------------


def test_continuous_chickpea_is_flagged_hard(reg):
    v = reg.check_rotation(["chickpea", "chickpea"], cyclic=False)
    assert v
    top = v[0]
    assert top.required_years == 5
    assert top.gap_years == 1
    assert top.severity == "high"
    assert "Fusarium" in top.driver
    assert not reg.rotation_is_feasible(["chickpea", "chickpea"], cyclic=False)


def test_cycle_wrap_is_detected(reg):
    """Read left to right this looks fine; it fails only when the rotation repeats.

    Wheat opens and closes the cycle, so the next pass replants it after a single
    season - the breach is invisible unless the checker closes the loop.
    """
    seq = ["wheat", "sunn_hemp", "chickpea", "wheat"]

    assert reg.check_rotation(seq, cyclic=False) == []

    wrapped = reg.check_rotation(seq, cyclic=True)
    assert wrapped, "wrap-around return of wheat after 1 year must be caught"
    assert all(v.wrapped for v in wrapped)
    assert wrapped[0].gap_years == 1
    assert wrapped[0].crop_a == "wheat" and wrapped[0].crop_b == "wheat"


def test_taxonomic_distance_does_not_imply_a_disease_break(reg):
    """Maize and cotton are unrelated yet share root-knot nematode.

    This is why the checker compares host ranges rather than families: a sequence
    that looks like a clean break on taxonomy alone can carry an organism straight
    through it.
    """
    maize, cotton = reg.crop("maize"), reg.crop("cotton")
    assert maize.family != cotton.family

    shared = {p.id for p in reg.shared_pathogens("maize", "cotton")}
    assert "meloidogyne_incognita" in shared

    v = reg.check_rotation(["maize", "cotton"], cyclic=False)
    assert v
    # Both shared organisms ask for 2 years, so which one is reported as the driver
    # is a tie; only require that it is genuinely one of them.
    assert v[0].driver in {reg.pathogen(pid).name for pid in shared}
    assert v[0].required_years == 2


def test_a_sound_four_slot_rotation_passes_cleanly(reg):
    """Rice - wheat broken by a green manure and a pulse: no shared hosts at all."""
    seq = ["rice_lowland", "wheat", "sunn_hemp", "chickpea"]
    assert reg.check_rotation(seq, cyclic=True) == []
    assert reg.rotation_is_feasible(seq)


def test_fallow_never_creates_a_conflict(reg):
    """Bare fallow hosts nothing; its cost is erosion and carbon, not disease."""
    assert reg.check_rotation(["chickpea", "fallow_bare", "chickpea"], cyclic=False)
    assert reg.check_rotation(["fallow_bare", "fallow_bare"], cyclic=True) == []
    seq = ["wheat", "fallow_bare", "maize", "fallow_bare"]
    assert [v for v in reg.check_rotation(seq) if "fallow" in (v.crop_a, v.crop_b)] == []


def test_double_cropping_gap_is_measured_in_years_not_slots(reg):
    """Two slots per year means slot distance 2 is only one year of break."""
    seq = ["potato", "wheat", "potato", "wheat"]

    yearly = reg.check_rotation(seq, slots_per_year=1, cyclic=False)
    double = reg.check_rotation(seq, slots_per_year=2, cyclic=False)

    potato_yearly = next(v for v in yearly if v.crop_a == "potato" and v.crop_b == "potato")
    potato_double = next(v for v in double if v.crop_a == "potato" and v.crop_b == "potato")

    assert potato_yearly.gap_years == 2
    assert potato_double.gap_years == 1
    assert potato_double.severity == "high"


def test_weak_efficacy_breaches_are_cautions_not_blockers(reg):
    """Refusing every broad-host breach would reject rotations farmers already run."""
    seq = ["mungbean", "wheat", "mungbean", "wheat"]
    v = reg.check_rotation(seq, cyclic=True)
    assert v
    assert all(x.severity == "caution" for x in v), [(x.driver, x.efficacy) for x in v]
    assert reg.rotation_is_feasible(seq)


def test_violations_describe_themselves(reg):
    v = reg.check_rotation(["mustard", "mustard"], cyclic=False)[0]
    text = v.describe()
    assert "Clubroot" in text and "7" in text
    assert "confidence" in text


def test_violations_sorted_by_strictness(reg):
    seq = ["potato", "chickpea", "potato", "chickpea"]
    v = reg.check_rotation(seq, cyclic=True)
    assert [x.required_years for x in v] == sorted(
        (x.required_years for x in v), reverse=True
    )


def test_bad_inputs_raise(reg):
    with pytest.raises(ParameterError):
        reg.check_rotation(["wheat", "sorghum_bicolor_typo"])
    with pytest.raises(ParameterError):
        reg.check_rotation(["wheat"], slots_per_year=0)


# --- the validator must reject an inconsistent pathogen entry -------------------


def test_validator_rejects_unknown_host(reg):
    p = dataclasses.replace(
        reg.pathogen("gaeumannomyces_take_all"), host_crops=("triticale",)
    )
    bad = dataclasses.replace(
        reg, pathogens={**reg.pathogens, "gaeumannomyces_take_all": p}
    )
    with pytest.raises(ParameterError, match="not in crops table"):
        _validate(bad)


def test_validator_rejects_interval_exceeding_persistence(reg):
    p = dataclasses.replace(
        reg.pathogen("gaeumannomyces_take_all"), recommended_return_interval_years=25
    )
    bad = dataclasses.replace(
        reg, pathogens={**reg.pathogens, "gaeumannomyces_take_all": p}
    )
    with pytest.raises(ParameterError, match="exceeds persistence"):
        _validate(bad)


def test_validator_rejects_ineffective_pathogen_claiming_efficacy(reg):
    p = dataclasses.replace(reg.pathogen("puccinia_rusts"), rotation_efficacy="strong")
    bad = dataclasses.replace(reg, pathogens={**reg.pathogens, "puccinia_rusts": p})
    with pytest.raises(ParameterError, match="rotation_effective is false"):
        _validate(bad)


def test_pathogen_ordering_is_deterministic(reg):
    """Equal intervals must not reorder between runs or the shown driver flickers."""
    for _ in range(3):
        assert [p.id for p in reg.shared_pathogens("maize", "cotton")] == [
            p.id for p in reg.shared_pathogens("maize", "cotton")
        ]
    ids = [p.id for p in reg.pathogens_for("chickpea")]
    assert ids == sorted(
        ids, key=lambda i: (-reg.pathogen(i).recommended_return_interval_years, i)
    )


# --- presence gating: we must not block on an organism we have not confirmed -----


def test_presence_tags_are_valid_and_complete(reg):
    for p in reg.pathogens.values():
        assert p.presence in {"widespread", "regional", "quarantine"}, p.id


def test_unconfirmed_regional_organism_only_cautions(reg):
    """Clubroot is field-specific; with no local report it cannot veto a rotation."""
    clubroot = reg.pathogen("plasmodiophora_brassicae")
    assert clubroot.presence == "regional"

    v = reg.check_rotation(["mustard", "mustard"], cyclic=False)[0]
    assert v.driver == "Clubroot"
    assert v.severity == "caution"
    assert reg.rotation_is_feasible(["mustard", "mustard"], cyclic=False)
    assert "not been confirmed locally" in v.describe()


def test_confirming_presence_escalates_to_blocking(reg):
    """Once an agronomist reports it, the same breach becomes a real blocker."""
    kw = {"cyclic": False, "confirmed_present": ["plasmodiophora_brassicae"]}
    v = reg.check_rotation(["mustard", "mustard"], **kw)[0]
    assert v.presence_confirmed
    assert v.severity == "high"
    assert not reg.rotation_is_feasible(["mustard", "mustard"], **kw)
    assert "not been confirmed locally" not in v.describe()


def test_striga_does_not_over_restrict_cereals_by_default(reg):
    """Striga is absent from most of Asia; it must not silently gate every cereal."""
    assert reg.pathogen("striga_asiatica").presence == "regional"

    v = reg.check_rotation(["maize", "pearl_millet"], cyclic=False)
    assert v and v[0].driver == "Witchweed (Striga)"
    assert v[0].severity == "caution"
    assert reg.rotation_is_feasible(["maize", "pearl_millet"], cyclic=False)

    # Confirmed, the same breach bites: 1 year of spacing against a 3-year
    # requirement is a 2-year shortfall, which is blocking.
    escalated = reg.check_rotation(
        ["maize", "pearl_millet"], cyclic=False, confirmed_present=["striga_asiatica"]
    )
    assert escalated[0].severity == "high"
    assert not reg.rotation_is_feasible(
        ["maize", "pearl_millet"], cyclic=False, confirmed_present=["striga_asiatica"]
    )


def test_widespread_organisms_still_block(reg):
    """Verticillium is assumed present wherever cotton grows, so this must reject."""
    assert reg.pathogen("verticillium_dahliae").presence == "widespread"
    seq = ["cotton", "wheat", "cotton", "wheat"]
    assert not reg.rotation_is_feasible(seq)
    assert any(v.severity == "high" for v in reg.check_rotation(seq))


def test_quarantine_pest_is_flagged_as_regulatory_not_just_severe(reg):
    """A regulated pest is a legal matter, tracked on its own axis from severity."""
    pcn = reg.pathogen("globodera_potato_cyst")
    assert pcn.is_regulated

    v = next(
        x
        for x in reg.check_rotation(["potato", "potato"], cyclic=False)
        if "cyst" in x.driver
    )
    assert v.regulatory is True
    assert v.severity == "high"
    assert "official guidance takes precedence" in v.describe()


def test_non_quarantine_violations_are_not_regulatory(reg):
    v = reg.check_rotation(["chickpea", "chickpea"], cyclic=False)[0]
    assert v.regulatory is False


def test_precautionary_intervals_are_marked_as_such(reg):
    """The displayed interval stays precautionary, but says that it is."""
    mustard = reg.min_return_interval("mustard")
    assert mustard.years == 7
    assert mustard.is_precautionary
    assert mustard.pathogen_id == "plasmodiophora_brassicae"

    chickpea = reg.min_return_interval("chickpea")
    assert not chickpea.is_precautionary


def test_confirmed_present_ids_are_validated(reg):
    with pytest.raises(ParameterError):
        reg.check_rotation(["wheat", "wheat"], confirmed_present=["not_a_pathogen"])


# --- one finding per crop pair ---------------------------------------------------


def test_one_violation_per_crop_pair(reg):
    """A 4-slot rotation pairs the same crops repeatedly; the report must not."""
    seq = ["potato", "mustard", "potato", "mustard"]
    v = reg.check_rotation(seq, cyclic=True)

    pairs = [tuple(sorted((x.crop_a, x.crop_b))) for x in v]
    assert len(pairs) == len(set(pairs)), pairs
    assert set(pairs) == {
        ("potato", "potato"),
        ("mustard", "mustard"),
        ("mustard", "potato"),
    }


def test_reported_violation_is_the_tightest_spacing(reg):
    """Of several spacings for one pair, the binding one is what the farmer needs."""
    seq = ["potato", "mustard", "potato", "mustard"]
    v = reg.check_rotation(seq, cyclic=True)
    cross = next(x for x in v if x.crop_a != x.crop_b)
    assert cross.gap_years == 1
