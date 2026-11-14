"""The HTTP API and the printable field plan.

The design rule under test: no endpoint returns a recommendation without the
evidence behind it. Ranking carries the weight-sensitivity report, rotation detail
carries the full evidence card, the crop screen carries the frequency behind every
verdict. An endpoint returning a bare ranking would be the thing this project set
out not to build.

Also pinned here is the split that makes the tool usable at all. Simulating a site
takes about a minute, so it is precomputed; re-ranking against stated priorities is
milliseconds, so it is live. A farmer moving a priority slider has to see the answer
move, and precomputing the preferences instead would have been the wrong way round.
"""

import pathlib

import time

import pytest
from fastapi.testclient import TestClient

from fieldshift.api.bundle import BUNDLE_DIR, SITES, available_sites
from fieldshift.api.main import app

BUNDLES = pathlib.Path(BUNDLE_DIR)

pytestmark = pytest.mark.skipif(
    not list(BUNDLES.glob("*.json")),
    reason="no precomputed bundles; build with python -m fieldshift.api.bundle",
)


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def site_id():
    return available_sites()[0]["id"]


# --- the bundle index cannot go stale --------------------------------------------


def test_every_bundle_on_disk_is_offered(client):
    """A written index dropped the pilot field when a subset was rebuilt.

    The site list is now derived from the files that exist, so it cannot drift away
    from them.
    """
    on_disk = {p.stem for p in BUNDLES.glob("*.json") if p.stem != "index"}
    offered = {s["id"] for s in client.get("/api/sites").json()["sites"]}
    assert offered == on_disk


def test_the_pilot_field_leads_the_list(client):
    sites = client.get("/api/sites").json()["sites"]
    if len(sites) > 1:
        assert sites[0]["id"] == SITES[0].id


def test_health_reports_what_is_available(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["sites"] >= 1
    assert body["hint"] is None


# --- site detail -----------------------------------------------------------------


def test_site_detail_carries_soil_weather_and_search_accounting(client, site_id):
    body = client.get(f"/api/site/{site_id}").json()
    site = body["site"]
    assert site["soil"]["available_water_mm_per_m"] > 50.0
    assert site["weather"]["years"] >= 35
    assert site["weather"]["et0_confidence"] in {"high", "medium", "low"}
    search = body["search"]
    assert search["examined"] > search["feasible"] >= search["on_front"] >= 1
    assert search["rejections"], "rejections must be explained, not hidden"
    assert body["rotations"] and body["front"]


def test_the_soil_says_whether_it_was_measured(client, site_id):
    soil = client.get(f"/api/site/{site_id}").json()["site"]["soil"]
    assert soil["measured"] is True
    assert "SSURGO" in soil["source"]


def test_a_missing_site_is_a_clear_404(client):
    response = client.get("/api/site/atlantis")
    assert response.status_code == 404
    assert "bundle" in response.json()["detail"]


# --- the live, interactive part --------------------------------------------------


def test_ranking_responds_to_stated_priorities(client, site_id):
    """The whole point of the precompute/live split: preferences must be cheap."""
    water = client.post("/api/rank", json={
        "site_id": site_id,
        "priorities": {"production": 1, "downside": 1, "irrigation": 5,
                       "carbon": 1, "wind_exposure": 1, "bare_ground": 1},
    }).json()
    carbon = client.post("/api/rank", json={
        "site_id": site_id,
        "priorities": {"production": 1, "downside": 1, "irrigation": 1,
                       "carbon": 5, "wind_exposure": 1, "bare_ground": 1},
    }).json()
    assert water["ranked"] and carbon["ranked"]
    water_top = water["ranked"][0]
    carbon_top = carbon["ranked"][0]
    # Prioritising water should not select a thirstier option than prioritising carbon.
    assert water_top["objectives"]["irrigation"] <= carbon_top["objectives"]["irrigation"]


def test_scores_are_ordered_best_first(client, site_id):
    ranked = client.post("/api/rank", json={"site_id": site_id}).json()["ranked"]
    scores = [r["score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)


def test_the_farmers_own_rotation_is_always_returned(client, site_id):
    """Even when it is dominated, they need to see where it sits."""
    body = client.post("/api/rank", json={"site_id": site_id,
                                          "front_only": True}).json()
    assert body["existing"] is not None
    assert body["existing"]["name"].startswith("EXISTING")


def test_every_ranking_carries_its_weight_sensitivity(client, site_id):
    """A recommendation that flips on one priority is an artefact of the weighting."""
    body = client.post("/api/rank", json={"site_id": site_id}).json()
    sens = body["sensitivity"]
    assert set(sens["stable"]) == {
        "production", "downside", "irrigation", "carbon",
        "wind_exposure", "bare_ground",
    }
    assert isinstance(sens["all_stable"], bool)
    if not sens["all_stable"]:
        assert sens["deciding_priorities"]


def test_every_ranking_carries_the_standing_caveat(client, site_id):
    body = client.post("/api/rank", json={"site_id": site_id}).json()
    assert "does not make one" in body["caveat"]


def test_what_beats_the_existing_rotation_is_reported(client, site_id):
    body = client.post("/api/rank", json={"site_id": site_id}).json()
    assert isinstance(body["beaten_by"], list)


def test_all_zero_priorities_are_refused(client, site_id):
    response = client.post("/api/rank", json={
        "site_id": site_id,
        "priorities": {k: 0 for k in
                       ("production", "downside", "irrigation", "carbon",
                        "wind_exposure", "bare_ground")},
    })
    assert response.status_code == 422


def test_out_of_range_priorities_are_refused(client, site_id):
    response = client.post("/api/rank", json={
        "site_id": site_id, "priorities": {"production": 99},
    })
    assert response.status_code == 422


def test_the_limit_is_respected(client, site_id):
    body = client.post("/api/rank", json={"site_id": site_id, "limit": 3}).json()
    assert len(body["ranked"]) <= 3


# --- trade-offs -------------------------------------------------------------------


def test_a_trade_off_splits_better_from_worse(client, site_id):
    front = client.get(f"/api/site/{site_id}").json()["front"]
    body = client.get(
        f"/api/site/{site_id}/rotation/{front[0]}/tradeoff"
    ).json()
    assert "better" in body and "worse" in body
    for item in body["better"] + body["worse"]:
        assert item["units"]
        assert item["label"]
    assert body["summary"]


# --- the honesty layer travels with the recommendation ---------------------------


def test_evidence_carries_confidence_and_the_limiting_input(client, site_id):
    body = client.get(f"/api/site/{site_id}/rotation/0/evidence").json()
    card = body["card"]
    assert card["confidence"] in {"high", "medium", "low"}
    if card["confidence"] != "high":
        assert card["limiting_sources"], "say which input caps it"
    assert card["sources"] and card["models"]
    assert card["unknowns"] and card["needs_local_review"]


def test_evidence_states_the_resolution_mismatch_as_a_number(client, site_id):
    card = client.get(f"/api/site/{site_id}/rotation/0/evidence").json()["card"]
    ratios = [s["mismatch_ratio"] for s in card["sources"] if s["mismatch_ratio"]]
    assert ratios and max(ratios) > 1000.0
    rendered = client.get(
        f"/api/site/{site_id}/rotation/0/evidence"
    ).json()["rendered"]
    assert "the field area" in rendered
    # Coarseness may be named, but never without the number beside it - "coarse"
    # on its own conveys nothing, which is the whole reason the ratio is computed.
    for line in rendered.splitlines():
        if "coarse" in line.lower():
            assert "the field area" in line, line


def test_evidence_always_names_the_wind_erosion_gap(client, site_id):
    rendered = client.get(
        f"/api/site/{site_id}/rotation/0/evidence"
    ).json()["rendered"]
    assert "wind erosion" in rendered
    assert "does not make one" in rendered


def test_the_crop_screen_reports_frequencies(client, site_id):
    body = client.get(f"/api/site/{site_id}/guardrails").json()
    assert body["crops"]
    for entry in body["crops"].values():
        assert "refused" in entry
        for verdict in entry["verdicts"]:
            assert verdict["level"] in {"pass", "warn", "refuse"}
    assert "warning rather than a refusal" in body["note"]


# --- the printable plan -----------------------------------------------------------


def test_the_field_plan_is_printable_html(client, site_id):
    response = client.get(f"/api/site/{site_id}/rotation/0/fieldplan")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    page = response.text
    assert "@page" in page and "size: A4" in page
    assert "<script" not in page, "it must print identically with no JavaScript"


def test_the_field_plan_carries_the_rotation_calendar(client, site_id):
    page = client.get(f"/api/site/{site_id}/rotation/0/fieldplan").text
    assert 'class="cal"' in page
    assert "Year 1" in page
    assert "Shaded = crop in the ground" in page


def test_the_field_plan_shows_risks_and_open_questions(client, site_id):
    page = client.get(f"/api/site/{site_id}/rotation/0/fieldplan").text
    assert "Risks, counted from" in page
    assert "Questions only you or your agronomist can answer" in page
    assert "does not make one" in page


def test_the_field_plan_states_the_resolution_caveat(client, site_id):
    page = client.get(f"/api/site/{site_id}/rotation/0/fieldplan").text
    assert "times this field" in page
    assert "comparison between rotations" in page


def test_crop_labels_are_readable_not_truncated(client, site_id):
    """Truncating the crop name gave "Winte" for winter wheat."""
    page = client.get(f"/api/site/{site_id}/rotation/0/fieldplan").text
    assert "Winte<" not in page


# --- a point with no bundle -------------------------------------------------------


def test_an_unknown_region_is_refused_before_any_work_starts(client):
    """Validation happens before the thread is spawned.

    A job that starts and then fails a minute later on an input we could have
    rejected immediately wastes the user's time and a core.
    """
    from fieldshift.api import jobs

    before = jobs.active_count()
    response = client.post("/api/analyse", json={
        "latitude": 39.1, "longitude": -101.3, "region": "atlantis",
    })
    assert response.status_code == 400
    assert "unknown region" in response.json()["detail"]
    assert jobs.active_count() == before, "no work should have been started"


def test_a_rotation_the_region_cannot_grow_is_refused(client):
    """Asking for rice on the High Plains is a question, not a job."""
    from fieldshift.api import jobs

    before = jobs.active_count()
    response = client.post("/api/analyse", json={
        "latitude": 39.1, "longitude": -101.3,
        "region": "us_high_plains", "current_rotation": ["rice_wheat"],
    })
    assert response.status_code == 400
    assert jobs.active_count() == before


def test_the_region_is_suggested_from_the_point_with_the_distance(client):
    """The suggestion has to come with how big a stretch it is.

    A calendar is not global, and borrowing one from 4,000 km away is a very
    different act from using one from 50 km away.
    """
    from fieldshift.api.jobs import suggest_region

    region_id, km, note = suggest_region(30.90, 75.85)  # Ludhiana, near Sangrur
    assert region_id == "in_punjab"
    assert km < 150
    assert "likely to apply" in note

    region_id, km, note = suggest_region(-33.9, 151.2)  # Sydney
    assert km > 5000
    assert "demonstration" in note


def test_a_finished_job_is_ranked_and_evidenced_through_the_same_code(
    client, monkeypatch
):
    """An on-demand result must not get a second, less careful code path.

    The physics is stubbed here with a real precomputed bundle: what is being
    tested is the job machinery and the shared ranking and evidence paths, not
    the simulation, which has its own tests.
    """
    from fieldshift.api import bundle as bundle_mod
    from fieldshift.api import jobs

    precomputed = bundle_mod.load_bundle("sangrur_pb")

    def fake_build(spec, **kw):
        on_stage = kw.get("on_stage")
        if on_stage:
            for stage in ("weather", "soil", "simulate", "rank"):
                on_stage(stage)
        return precomputed

    monkeypatch.setattr(jobs, "build_bundle", fake_build)

    started = client.post("/api/analyse", json={
        "latitude": 30.90, "longitude": 75.85,
        "region": "in_punjab", "current_rotation": ["rice_wheat"],
        "field_hectares": 3.0,
    })
    assert started.status_code == 200
    job_id = started.json()["job_id"]
    assert "Indian Punjab" in started.json()["message"]

    for _ in range(200):
        status = client.get(f"/api/analyse/{job_id}").json()
        if status["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert status["status"] == "done", status.get("error")
    assert status["result"]["site"]["name"]

    ranked = client.post(f"/api/analyse/{job_id}/rank",
                         json={"site_id": "live"})
    assert ranked.status_code == 200
    assert ranked.json()["ranked"]

    card = client.get(f"/api/analyse/{job_id}/rotation/0/evidence").json()["card"]
    assert card["confidence"] == "low"
    assert any("analysed on demand" in u.lower() for u in card["unknowns"]), (
        "an on-demand result must say that nobody checked its calendar"
    )

    plan = client.get(f"/api/analyse/{job_id}/rotation/0/fieldplan")
    assert plan.status_code == 200 and "<html" in plan.text.lower()


def test_polling_an_unfinished_job_explains_rather_than_erroring(client):
    response = client.post(f"/api/analyse/{'0' * 32}/rank", json={"site_id": "x"})
    assert response.status_code == 404
    assert "no job" in response.json()["detail"]


def test_impossible_coordinates_are_refused(client):
    response = client.post(
        "/api/analyse", json={"latitude": 200.0, "longitude": 0.0}
    )
    assert response.status_code == 422


# --- the web app is served --------------------------------------------------------


def test_the_interface_is_served_at_the_root(client):
    response = client.get("/")
    assert response.status_code == 200
    page = response.text
    assert "FieldShift" in page
    assert "Rethink What Grows Next" in page
    assert "leaflet" in page.lower()


def test_the_interface_shows_the_caveat_without_interaction(client):
    """It must be visible in the markup, not only after a request succeeds."""
    page = client.get("/").text
    assert "does not make one" in page
    assert "NASA POWER" in page


def test_site_detail_carries_the_region_block(client):
    """Without it the interface cannot tell a validated field from a transferred one.

    This is not a cosmetic field. The page falls back to assuming tier 1 when the
    block is absent, which badged the Punjab field as "Tier 1 - validated" against
    observed yields that do not exist - the exact overclaim the two-tier design
    exists to prevent.
    """
    for site in client.get("/api/sites").json()["sites"]:
        data = client.get(f"/api/site/{site['id']}").json()
        assert "region" in data, f"{site['id']} has no region block"
        region = data["region"]
        assert region["tier"] == site["tier"], (
            f"{site['id']}: site list says tier {site['tier']} but the detail "
            f"says tier {region['tier']}"
        )
        assert region["validated"] == site["validated"]
        # and tier 2 must never report itself as validated
        if region["tier"] == 2:
            assert region["validated"] is False
            assert region["yield_validation"] is None


def test_an_unvalidated_site_says_so_in_its_evidence(client):
    """The downgrade has to reach the artefact a judge or agronomist actually reads."""
    sites = client.get("/api/sites").json()["sites"]
    tier2 = [s for s in sites if s["tier"] == 2]
    if not tier2:
        pytest.skip("no tier 2 site built")
    card = client.get(f"/api/site/{tier2[0]['id']}/rotation/0/evidence").json()["card"]
    assert card["confidence"] == "low"
    names = {s["name"] for s in card["sources"]}
    # it must not cite US-only datasets it never touched
    assert "USDA SSURGO" not in names
    assert "USDA NASS QuickStats" not in names
    assert any("SoilGrids" in n for n in names)
