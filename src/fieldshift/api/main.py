"""The FieldShift HTTP API.

Design rule applied throughout: no endpoint returns a recommendation without the
evidence behind it. Ranking responses carry the weight-sensitivity report; rotation
detail carries the full evidence card; the crop screen carries the frequency behind
every verdict. An endpoint that returned a bare ranking would be the thing this
project set out not to build.

    PYTHONPATH=src ./.venv/bin/uvicorn fieldshift.api.main:app --reload
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from ..data import load_registry
from ..decide.evidence import NASADEM, build_card
from ..decide.guardrails import Level, Verdict
from ..report.fieldplan import render_field_plan
from . import jobs
from .bundle import SITES_BY_ID, available_sites, load_bundle
from .jobs import suggest_region
from .regions import REGIONS, RegionError, region
from .schemas import (
    AnalyseStarted,
    AnalyseRequest,
    RankRequest,
    RankResponse,
    SensitivityReport,
    TradeOffResponse,
)
from .service import (
    ServiceError,
    beaten_by,
    deciding_priorities,
    existing_index,
    rank_site,
    trade_off_against_existing,
)

WEB_DIR = Path(__file__).resolve().parents[3] / "web"

app = FastAPI(
    title="FieldShift",
    description=(
        "Rethink What Grows Next. A crop-rotation decision-support tool built on "
        "NASA Earth observations. Every recommendation carries the datasets, "
        "equations, validation results and unknowns behind it."
    ),
    version="0.1.0",
)


def _bundle(site_id: str) -> dict:
    try:
        return load_bundle(site_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from None


def _verdicts_from_screen(screen: dict) -> list[Verdict]:
    out = []
    for entry in screen.values():
        for v in entry["verdicts"]:
            out.append(
                Verdict(
                    rule=v["rule"], level=Level(v["level"]), message=v["message"],
                    frequency=v.get("frequency"),
                    needs_local_review=v.get("needs_local_review", False),
                )
            )
    return out


# ---------------------------------------------------------------- meta


@app.get("/api/health")
def health() -> dict:
    sites = available_sites()
    return {
        "status": "ok" if sites else "no bundles built",
        "sites": len(sites),
        "hint": (
            None if sites else
            "build one with: PYTHONPATH=src python -m fieldshift.api.bundle"
        ),
    }


@app.get("/api/sites")
def sites() -> dict:
    found = available_sites()
    if not found:
        raise HTTPException(
            503,
            "no precomputed sites. Simulating a site takes about a minute, so it is "
            "done ahead of time: PYTHONPATH=src python -m fieldshift.api.bundle",
        )
    return {"sites": found}


@app.get("/api/site/{site_id}")
def site_detail(site_id: str) -> dict:
    data = _bundle(site_id)
    return {
        "site": data["site"],
        # The region block is not optional. Without it the interface cannot tell a
        # validated field from a transferred one, and it falls back to assuming
        # tier 1 - which badged a Punjab field as "validated" against observed
        # yields that do not exist. A test pins this.
        "region": data.get("region", {"id": "us_high_plains", "name": "US High Plains",
                                      "tier": 1, "validated": True}),
        "objectives": data["objectives"],
        "search": data["search"],
        "rotations": data["rotations"],
        "front": data["front"],
    }


# ---------------------------------------------------------------- the live part


@app.post("/api/rank", response_model=RankResponse)
def rank(request: RankRequest) -> RankResponse:
    """Rank rotations against stated priorities. This is the interactive endpoint.

    Cheap by design: the simulation is already done, so moving a priority slider
    re-ranks in milliseconds rather than re-running forty-one years of physics.
    """
    return rank_bundle(_bundle(request.site_id), request)


def rank_bundle(data: dict, request: RankRequest) -> RankResponse:
    """Rank one already-simulated bundle.

    Takes the bundle rather than a site id so an on-demand analysis, whose result
    has the same shape but lives in memory rather than on disk, re-ranks through
    exactly the same code.
    """
    if request.priorities.total() <= 0:
        raise HTTPException(422, "at least one priority must be above zero")
    try:
        ranked = rank_site(
            request.site_id, request.priorities.as_weights(),
            front_only=request.front_only, bundle=data,
        )
    except ServiceError as exc:
        raise HTTPException(422, str(exc)) from None

    def as_row(index: int, score: float) -> dict:
        row = ranked.rows[index]
        return {
            "index": row["index"], "name": row["name"], "score": score,
            "is_existing": row["is_existing"], "on_front": index in ranked.front,
            "cycle_years": row["cycle_years"], "crops": row["crops"],
            "objectives": row["objectives"], "detail": row["detail"],
        }

    ordered = [as_row(i, s) for i, s in ranked.order][: request.limit]
    current = existing_index(ranked)
    existing = None
    if current is not None:
        score = next((s for i, s in ranked.order if i == current), 0.0)
        existing = as_row(current, score)

    deciding = deciding_priorities(ranked.stability)
    return RankResponse(
        site_id=request.site_id,
        site_name=ranked.site["name"],
        ranked=ordered,
        existing=existing,
        beaten_by=list(beaten_by(ranked)),
        sensitivity=SensitivityReport(
            stable=ranked.stability,
            all_stable=not deciding,
            deciding_priorities=deciding,
        ),
    )


@app.get("/api/site/{site_id}/rotation/{index}/tradeoff", response_model=TradeOffResponse)
def tradeoff(site_id: str, index: int) -> TradeOffResponse:
    """What switching from the current rotation to this one costs and gains."""
    return _tradeoff_for(_bundle(site_id), site_id, index)


def _tradeoff_for(data: dict, site_id: str, index: int) -> TradeOffResponse:
    """Trade-off from a bundle, so on-demand results use the same comparison."""
    try:
        ranked = rank_site(site_id, {o["key"]: 1.0 for o in data["objectives"]},
                           front_only=False, bundle=data)
        target = next(
            (i for i, r in enumerate(ranked.rows) if r["index"] == index), None
        )
        if target is None:
            raise HTTPException(404, f"no rotation with index {index}")
        return TradeOffResponse(**trade_off_against_existing(ranked, target))
    except ServiceError as exc:
        raise HTTPException(422, str(exc)) from None


# ---------------------------------------------------------------- the honesty layer


@app.get("/api/site/{site_id}/rotation/{index}/evidence")
def evidence(site_id: str, index: int) -> dict:
    return _evidence_for(_bundle(site_id), index)


def _evidence_for(data: dict, index: int, extra_unknowns=()) -> dict:
    """The full evidence card: datasets, equations, validation, unknowns.

    Takes the bundle rather than a site id so an on-demand result, which lives in
    memory rather than on disk, produces its card through exactly the same path
    as a precomputed site.
    """
    row = next((r for r in data["rotations"] if r["index"] == index), None)
    if row is None:
        raise HTTPException(404, f"no rotation with index {index}")

    site = data["site"]
    existing = next((r for r in data["rotations"] if r["is_existing"]), None)
    numbers = []
    for objective in data["objectives"]:
        key, label, units = objective["key"], objective["label"], objective["units"]
        value = row["objectives"].get(key)
        if value is None:
            continue
        if existing and key in existing["objectives"]:
            numbers.append(
                (label, f"{existing['objectives'][key]:.3g} -> {value:.3g} {units}")
            )
        else:
            numbers.append((label, f"{value:.3g} {units}"))
    numbers.append(("crops harvested per decade", f"{row['detail']['crops_per_decade']:.1f}"))

    # Sources come from the region, not from this endpoint. Hardcoding them here
    # claimed SSURGO and NASS county yields for every site, which on a site
    # outside the United States would have been a false citation on the one
    # artefact whose whole job is to be checkable.
    region_spec = region(data.get("region", {}).get("id", "us_high_plains"))
    sources = [
        s for s in region_spec.sources
        if s is not NASADEM or site["terrain"]["slope_percent"] is not None
    ]

    extra = []
    if site["terrain"]["slope_percent"] is None:
        extra.append(site["terrain"]["note"])
    if not region_spec.is_validated:
        extra.append(
            f"This is a {region_spec.name} site and nothing here has been checked "
            "against observed yields. The engine is the same one validated on the "
            "US High Plains; the inputs are coarser and unverified. Treat the "
            "ranking as a hypothesis to test locally, not a prediction."
        )
    extra.extend(region_spec.review_first)
    if row["detail"]["nitrogen_limited_seasons"]:
        extra.append(
            f"{int(row['detail']['nitrogen_limited_seasons'])} simulated seasons were "
            "limited by nitrogen rather than water, which calls for a different "
            "response than a water constraint"
        )

    card = build_card(
        f"{row['name']} at {site['name']}",
        field_hectares=site.get("field_hectares", 65.0),
        numbers=tuple(numbers),
        sources=tuple(sources),
        model_keys=("et0", "water", "carbon", "nitrogen", "erosion", "rotation"),
        guardrails=tuple(_verdicts_from_screen(data["guardrails"])),
        extra_unknowns=tuple(extra) + tuple(extra_unknowns),
    )
    return {"card": card.to_dict(), "rendered": card.render()}


@app.get("/api/validation")
def validation() -> dict:
    """Every validation result, from one file.

    Served rather than written into the interface so the page and the record
    cannot drift apart. The file carries the reproducing command for each check,
    which is the only reason any of these numbers are worth showing.
    """
    path = Path("data/validation.json")
    if not path.exists():
        raise HTTPException(404, "validation record not found")
    return json.loads(path.read_text())


@app.get("/api/site/{site_id}/guardrails")
def guardrails(site_id: str) -> dict:
    """The crop screen, with the frequency behind every verdict."""
    data = _bundle(site_id)
    return {
        "site_name": data["site"]["name"],
        "crops": data["guardrails"],
        "note": (
            "Frequencies come from the observed record. Almost everything is a "
            "warning rather than a refusal: a screen that refuses freely removes "
            "options a farmer may have good local reasons to want."
        ),
    }


@app.get("/api/site/{site_id}/rotation/{index}/fieldplan", response_class=HTMLResponse)
def fieldplan(site_id: str, index: int) -> str:
    """A printable one-page field plan - the artefact a farmer keeps."""
    data = _bundle(site_id)
    row = next((r for r in data["rotations"] if r["index"] == index), None)
    if row is None:
        raise HTTPException(404, f"no rotation with index {index}")
    registry = load_registry()
    return render_field_plan(data, row, registry)


@app.get("/api/regions")
def regions() -> dict:
    """The cropping calendars available, and what each one can grow.

    A caller has to pick one before an on-demand run, because a calendar is not
    global: the same engine with the wrong sowing dates produces confident
    nonsense. Every pattern is listed so the choice can be made on what is
    actually grown rather than on a region name.
    """
    return {
        "regions": [
            {
                "id": r.id,
                "name": r.name,
                "tier": r.tier,
                "validated": r.is_validated,
                "note": r.note,
                "soil_source": r.soil_source,
                "patterns": [
                    {
                        "id": p.id,
                        "name": p.name,
                        "notes": p.notes,
                        "crops": list(p.crop_ids),
                        "irrigated": p.is_irrigated,
                        "fallow": p.is_fallow_year,
                    }
                    for p in r.patterns
                ],
            }
            for r in REGIONS
        ],
        "note": (
            "Sowing dates belong to the region, not to the point. Running a "
            "calendar far from where it was written is a demonstration, not a "
            "recommendation, and the result will say so."
        ),
    }


@app.post("/api/analyse", response_model=AnalyseStarted)
def analyse(request: AnalyseRequest) -> AnalyseStarted:
    """Start an on-demand analysis for any point on Earth.

    Returns immediately with a job id. A fresh point costs roughly a minute of
    weather download and a minute of simulation, which is too long to hold a
    request open, so the client polls ``/api/analyse/{job_id}``.
    """
    region_id = request.region
    suggested, km, confidence = suggest_region(request.latitude, request.longitude)
    if region_id is None:
        region_id = suggested
    try:
        job = jobs.start(
            request.latitude, request.longitude, region_id,
            existing_rotation=tuple(request.current_rotation),
            field_hectares=request.field_hectares,
            cycle_years=request.cycle_years,
        )
    except RegionError as exc:
        raise HTTPException(400, str(exc)) from exc
    except jobs.JobError as exc:
        raise HTTPException(429, str(exc)) from exc

    where = (
        f"Using the {region(region_id).name} calendar. "
        f"The nearest field we have run is {km:,.0f} km away - {confidence}."
    )
    return AnalyseStarted(
        job_id=job.id,
        status=job.status,
        message=where,
        poll=f"/api/analyse/{job.id}",
    )


@app.get("/api/analyse/{job_id}")
def analyse_status(job_id: str) -> dict:
    """Progress, then the finished bundle.

    The finished result has the same shape as a precomputed site bundle, so every
    other endpoint and the whole interface can read it without a special case.
    """
    try:
        return jobs.get(job_id).public()
    except jobs.JobError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/analyse/{job_id}/rank", response_model=RankResponse)
def analyse_rank(job_id: str, request: RankRequest) -> RankResponse:
    """Rank a finished on-demand analysis against stated priorities."""
    job = _finished_job(job_id)
    return rank_bundle(job.result, request)


@app.get("/api/analyse/{job_id}/rotation/{index}/evidence")
def analyse_evidence(job_id: str, index: int) -> dict:
    """The evidence card for an on-demand result.

    An on-demand run carries an extra source the precomputed sites do not: it was
    produced for a point chosen by the user, possibly far from anywhere the
    calendar was written for, and that has to appear on the card rather than in a
    tooltip somebody closed.
    """
    job = _finished_job(job_id)
    extra = [
        "This field was analysed on demand. No one has checked its cropping "
        "calendar, its sowing dates or its nitrogen rates against local practice.",
    ]
    return _evidence_for(job.result, index, extra_unknowns=extra)


@app.get("/api/analyse/{job_id}/rotation/{index}/fieldplan",
         response_class=HTMLResponse)
def analyse_fieldplan(job_id: str, index: int) -> str:
    """The printable plan for an on-demand result.

    The paper artefact is the point of the whole tool, so it must not be a
    privilege of the five precomputed fields.
    """
    job = _finished_job(job_id)
    row = next((r for r in job.result["rotations"] if r["index"] == index), None)
    if row is None:
        raise HTTPException(404, f"no rotation with index {index}")
    return render_field_plan(job.result, row, load_registry())


@app.get("/api/analyse/{job_id}/rotation/{index}/tradeoff",
         response_model=TradeOffResponse)
def analyse_tradeoff(job_id: str, index: int) -> TradeOffResponse:
    """What an on-demand option gives and takes against the current rotation."""
    job = _finished_job(job_id)
    return _tradeoff_for(job.result, job.result["site"]["id"], index)


def _finished_job(job_id: str):
    try:
        job = jobs.get(job_id)
    except jobs.JobError as exc:
        raise HTTPException(404, str(exc)) from exc
    if job.status == "failed":
        raise HTTPException(409, f"that analysis failed: {job.error}")
    if job.status != "done":
        raise HTTPException(
            409,
            f"that analysis is still {job.status} ({job.progress:.0%}). "
            f"Poll /api/analyse/{job_id} until it reports done.",
        )
    return job


# ---------------------------------------------------------------- the web app

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> FileResponse:
        return FileResponse(str(WEB_DIR / "index.html"))
