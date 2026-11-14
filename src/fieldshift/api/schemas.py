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

    Deliberately limited. A full search is about a minute of simulation, so this
    endpoint reports what it would take rather than blocking a request for that
    long and timing out somewhere unhelpful.
    """

    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    field_hectares: float = Field(65.0, gt=0.0, le=10_000.0)
