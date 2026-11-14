"""The FieldShift HTTP API.

Design rule applied throughout: no endpoint returns a recommendation without the
evidence behind it. Ranking responses carry the weight-sensitivity report; rotation
detail carries the full evidence card; the crop screen carries the frequency behind
every verdict. An endpoint that returned a bare ranking would be the thing this
project set out not to build.

    PYTHONPATH=src ./.venv/bin/uvicorn fieldshift.api.main:app --reload
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from ..data import load_registry
from ..decide.evidence import (
    CROP_TABLE,
    GRIDMET,
    NASADEM,
    POWER,
    QUICKSTATS,
    SSURGO,
    build_card,
)
from ..decide.guardrails import Level, Verdict
from ..report.fieldplan import render_field_plan
from .bundle import SITES_BY_ID, available_sites, load_bundle
from .schemas import (
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
    if request.priorities.total() <= 0:
        raise HTTPException(422, "at least one priority must be above zero")
    data = _bundle(request.site_id)
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
    data = _bundle(site_id)
    try:
        ranked = rank_site(site_id, {k: 1.0 for k in data["objectives"] and
                                     [o["key"] for o in data["objectives"]]},
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
    """The full evidence card: datasets, equations, validation, unknowns."""
    data = _bundle(site_id)
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

    sources = [POWER, SSURGO, CROP_TABLE, QUICKSTATS, GRIDMET]
    if site["terrain"]["slope_percent"] is not None:
        sources.append(NASADEM)

    extra = []
    if site["terrain"]["slope_percent"] is None:
        extra.append(site["terrain"]["note"])
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
        extra_unknowns=tuple(extra),
    )
    return {"card": card.to_dict(), "rendered": card.render()}


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


@app.post("/api/analyse")
def analyse(request: AnalyseRequest) -> dict:
    """A point with no precomputed bundle.

    Honest about cost rather than blocking a request for a minute and timing out
    somewhere unhelpful.
    """
    nearest = min(
        SITES_BY_ID.values(),
        key=lambda s: (s.latitude - request.latitude) ** 2
        + (s.longitude - request.longitude) ** 2,
    )
    distance_deg = (
        (nearest.latitude - request.latitude) ** 2
        + (nearest.longitude - request.longitude) ** 2
    ) ** 0.5
    return {
        "accepted": False,
        "reason": (
            "A full analysis fetches 41 years of weather, reads the soil survey, and "
            "simulates about 80 candidate rotations through every year - roughly a "
            "minute of computation. That is done ahead of time rather than inside a "
            "request."
        ),
        "nearest_precomputed": {
            "id": nearest.id,
            "name": nearest.name,
            "approx_degrees_away": round(distance_deg, 2),
        },
        "how_to_add_this_point": (
            "Add a SiteSpec to fieldshift/api/bundle.py and run "
            "PYTHONPATH=src python -m fieldshift.api.bundle <site_id>"
        ),
        "what_would_be_needed": [
            "NASA POWER daily weather for the point, 1984-2024 (no credentials)",
            "USDA SSURGO soil at the point (US only, no credentials)",
            "a NASADEM tile for slope (Earthdata login)",
            "USDA NASS county yields to validate against (free API key)",
        ],
    }


# ---------------------------------------------------------------- the web app

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> FileResponse:
        return FileResponse(str(WEB_DIR / "index.html"))
