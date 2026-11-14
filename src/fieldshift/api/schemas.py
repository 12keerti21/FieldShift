"""Request and response shapes for the FieldShift API.

Every response that carries a recommendation also carries its confidence and what
needs local review. That is enforced by the schema rather than left to each
endpoint, because an endpoint that forgot would produce exactly the bare
recommendation this project exists to avoid.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

OBJECTIVE_KEYS = (
    "production", "downside", "irrigation", "carbon", "wind_exposure", "bare_ground"
)


class Priorities(BaseModel):
    """What the farmer cares about, as relative weights.

    Weights are relative, not absolute - they are normalised before use, so a farmer
    setting everything to 10 gets the same answer as one setting everything to 1.
    """

    production: float = Field(1.0, ge=0.0, le=10.0)
    downside: float = Field(1.0, ge=0.0, le=10.0)
    irrigation: float = Field(1.0, ge=0.0, le=10.0)
    carbon: float = Field(1.0, ge=0.0, le=10.0)
    wind_exposure: float = Field(1.0, ge=0.0, le=10.0)
    bare_ground: float = Field(1.0, ge=0.0, le=10.0)

    def as_weights(self) -> dict[str, float]:
        return {k: getattr(self, k) for k in OBJECTIVE_KEYS}

    @field_validator("*")
    @classmethod
    def _finite(cls, v: float) -> float:
        if v != v:  # NaN
            raise ValueError("weights must be finite")
        return v

    def total(self) -> float:
        return sum(self.as_weights().values())


class RankRequest(BaseModel):
    site_id: str
    priorities: Priorities = Priorities()
    front_only: bool = Field(
        True,
        description=(
            "Rank only the Pareto front. A dominated rotation is beaten on every "
            "count, so including it can only mislead."
        ),
    )
    limit: int = Field(8, ge=1, le=50)


class RankedRotation(BaseModel):
    index: int
    name: str
    score: float
    is_existing: bool
    on_front: bool
    cycle_years: int
    crops: list[str]
    objectives: dict[str, float]
    detail: dict[str, float]


class SensitivityReport(BaseModel):
    """Whether the top choice survives shifting each stated priority.

    A recommendation that flips when one priority moves by half is an artefact of
    the weighting, and the farmer should be told which of their own priorities is
    deciding the answer.
    """

    stable: dict[str, bool]
    all_stable: bool
    deciding_priorities: list[str]


class RankResponse(BaseModel):
    site_id: str
    site_name: str
    ranked: list[RankedRotation]
    existing: RankedRotation | None
    beaten_by: list[str] = Field(
        default_factory=list,
        description="Rotations that beat the existing one on every objective",
    )
    sensitivity: SensitivityReport
    caveat: str = (
        "FieldShift supports a decision; it does not make one. Nothing here "
        "replaces local agronomic judgement."
    )


class TradeOffItem(BaseModel):
    objective: str
    label: str
    units: str
    change: float
    better: bool


class TradeOffResponse(BaseModel):
    from_name: str
    to_name: str
    better: list[TradeOffItem]
    worse: list[TradeOffItem]
    summary: str


class AnalyseRequest(BaseModel):
    """A live run for a point with no precomputed bundle.

    A fresh point costs roughly a minute of weather download and a minute of
    simulation, so this starts a job and returns immediately rather than blocking
    a request long enough to time out behind any normal proxy.
    """

    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    field_hectares: float = Field(10.0, gt=0.0, le=10_000.0)
    region: str | None = Field(
        None,
        description=(
            "Which region's cropping calendar to apply. Left out, the nearest "
            "region is suggested - but a calendar is not global, and sowing winter "
            "wheat in October is right in Kansas and wrong in Punjab."
        ),
    )
    current_rotation: list[str] = Field(
        default_factory=list,
        description=(
            "The year patterns the farmer grows now, in order. This is what every "
            "alternative gets compared against, so without it the tool can rank "
            "options but cannot tell anyone whether to change."
        ),
    )
    cycle_years: int = Field(3, ge=1, le=4)

    @field_validator("current_rotation")
    @classmethod
    def _not_too_long(cls, v: list[str]) -> list[str]:
        if len(v) > 6:
            raise ValueError("a current rotation of more than six years is not a cycle")
        return v


class AnalyseStarted(BaseModel):
    job_id: str
    status: str
    message: str
    poll: str
    estimated_seconds: int = 120


class RegionSummary(BaseModel):
    """What a caller needs to pick a region before starting a run."""

    id: str
    name: str
    tier: int
    validated: bool
    note: str
    soil_source: str
    patterns: list[dict]
