"""On-demand analysis for a point with no precomputed bundle.

The precompute/live split exists because simulation is slow and ranking is fast,
and that split is still right. But it left a gap: a farmer whose field is not one
of the five precomputed sites could not use the tool at all. This closes that gap
honestly rather than by pretending the work is cheap.

A fresh point costs roughly:

    NASA POWER, 41 years daily      20-60 s   (network, then cached)
    soil (SSURGO or SoilGrids)       2-30 s   (network, then cached)
    simulate every candidate         40-90 s   (CPU)

So it is a job, not a request. The HTTP call returns a job id immediately and the
client polls. Blocking a request for two minutes would time out behind any normal
proxy and the user would be told nothing about why.

Jobs live in memory and die with the process. That is a deliberate limit, not an
oversight: persisting them would mean a queue, a worker and a store, which is a
different piece of software from the one this needs to be. It is stated in the API
response so nobody builds on an assumption it does not hold.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

from .bundle import SiteSpec, build_bundle
from .regions import REGIONS, region

Status = Literal["queued", "running", "done", "failed"]

# Where the time actually goes, so the client can show honest progress rather than
# a spinner that implies nothing is happening.
STAGES = (
    ("weather", "Fetching 41 years of NASA POWER weather", 0.30),
    ("soil", "Looking up the soil at this point", 0.15),
    ("simulate", "Simulating every candidate rotation", 0.50),
    ("rank", "Scoring and finding the trade-off front", 0.05),
)

MAX_ACTIVE_JOBS = 4
JOB_TTL_SECONDS = 3600.0


class JobError(ValueError):
    pass


@dataclass
class Job:
    id: str
    latitude: float
    longitude: float
    region_id: str
    status: Status = "queued"
    stage: str = "queued"
    message: str = "Waiting to start"
    progress: float = 0.0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: dict | None = None
    error: str | None = None

    @property
    def elapsed_s(self) -> float:
        return (self.finished_at or time.time()) - self.started_at

    def public(self) -> dict:
        out = {
            "job_id": self.id,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "progress": round(self.progress, 3),
            "elapsed_seconds": round(self.elapsed_s, 1),
            "latitude": self.latitude,
            "longitude": self.longitude,
            "region": self.region_id,
            "note": (
                "Jobs are held in memory and are lost if the server restarts. "
                "Precomputed sites are not."
            ),
        }
        if self.status == "failed":
            out["error"] = self.error
        if self.status == "done":
            out["result"] = self.result
        return out


_JOBS: dict[str, Job] = {}
_LOCK = threading.Lock()


def _advance(job: Job, key: str) -> None:
    done = 0.0
    for name, message, weight in STAGES:
        if name == key:
            job.stage = name
            job.message = message
            job.progress = done
            return
        done += weight
    job.progress = done


def suggest_region(latitude: float, longitude: float) -> tuple[str, float, str]:
    """Which region's calendar applies here, and how far away its nearest field is.

    A region carries a cropping calendar, and a calendar is not global: sowing
    winter wheat in October is right in Kansas and wrong in Punjab. There is no way
    to infer the right calendar for an arbitrary point from weather alone, so this
    suggests the nearest region we actually have and reports the distance so the
    caller can see how big a stretch it is. The user can override it.
    """
    from .bundle import SITES

    best, best_km = None, float("inf")
    for spec in SITES:
        km = _haversine_km(latitude, longitude, spec.latitude, spec.longitude)
        if km < best_km:
            best, best_km = spec, km
    if best is None:
        return REGIONS[0].id, float("nan"), "no precomputed site to compare against"

    if best_km <= 300:
        confidence = "the calendar for this region is likely to apply"
    elif best_km <= 1500:
        confidence = (
            "this is well outside any field we have run, so the sowing dates are "
            "borrowed and should be checked before the result is used"
        )
    else:
        confidence = (
            "this is thousands of kilometres from any field we have run. The "
            "physics still applies, but the cropping calendar almost certainly "
            "does not, and the result should be treated as a demonstration"
        )
    return best.region, best_km, confidence


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    p1, p2 = radians(lat1), radians(lat2)
    a = (sin((p2 - p1) / 2) ** 2
         + cos(p1) * cos(p2) * sin(radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * asin(sqrt(a))


def _run(job: Job, existing: tuple[str, ...], field_hectares: float,
         cycle_years: int, max_candidates: int) -> None:
    try:
        reg = region(job.region_id)
        spec = SiteSpec(
            id=f"live_{job.id[:8]}",
            name=f"{job.latitude:.4f}, {job.longitude:.4f}",
            latitude=job.latitude, longitude=job.longitude,
            elevation_m=0.0,  # replaced from POWER's own reported elevation
            state="", county="",
            note=(
                f"Analysed on demand against the {reg.name} cropping calendar. "
                "Not a precomputed field."
            ),
            existing_rotation=existing,
            region=job.region_id,
            field_hectares=field_hectares,
        )
        job.status = "running"
        _advance(job, "weather")

        def progress(stage: str) -> None:
            _advance(job, stage)

        bundle = build_bundle(
            spec, cycle_years=cycle_years, max_candidates=max_candidates,
            on_stage=progress,
        )
        job.result = bundle
        job.status = "done"
        job.stage = "done"
        job.message = "Analysis complete"
        job.progress = 1.0
    except Exception as exc:  # surfaced to the caller rather than swallowed
        job.status = "failed"
        job.stage = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        job.message = "Analysis failed"
    finally:
        job.finished_at = time.time()


def _reap() -> None:
    """Drop finished jobs older than the TTL so memory does not grow without end."""
    now = time.time()
    for job_id, job in list(_JOBS.items()):
        if job.finished_at and now - job.finished_at > JOB_TTL_SECONDS:
            _JOBS.pop(job_id, None)


def start(
    latitude: float,
    longitude: float,
    region_id: str,
    *,
    existing_rotation: tuple[str, ...] = (),
    field_hectares: float = 10.0,
    cycle_years: int = 3,
    max_candidates: int = 90,
) -> Job:
    """Queue an analysis and return immediately."""
    reg = region(region_id)  # raises for an unknown region, before any work starts
    for pattern_id in existing_rotation:
        reg.pattern(pattern_id)  # raises for a rotation this region cannot grow

    with _LOCK:
        _reap()
        active = sum(1 for j in _JOBS.values() if j.status in ("queued", "running"))
        if active >= MAX_ACTIVE_JOBS:
            raise JobError(
                f"{active} analyses are already running and each one occupies a "
                "core for about a minute. Wait for one to finish rather than "
                "queueing work that would only make them all slower."
            )
        job = Job(id=uuid.uuid4().hex, latitude=latitude, longitude=longitude,
                  region_id=region_id)
        _JOBS[job.id] = job

    thread = threading.Thread(
        target=_run, args=(job, existing_rotation, field_hectares, cycle_years,
                           max_candidates),
        daemon=True, name=f"analyse-{job.id[:8]}",
    )
    thread.start()
    return job


def get(job_id: str) -> Job:
    job = _JOBS.get(job_id)
    if job is None:
        raise JobError(
            f"no job {job_id!r}. Jobs are held in memory for an hour and are lost "
            "if the server restarts."
        )
    return job


def active_count() -> int:
    return sum(1 for j in _JOBS.values() if j.status in ("queued", "running"))
