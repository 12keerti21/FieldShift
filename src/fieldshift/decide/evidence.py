"""Evidence cards: everything needed to audit one recommendation.

A recommendation a farmer cannot interrogate is not decision support, it is an
instruction. So every claim travels with the datasets behind it, the equations that
turned them into numbers, what the validation showed, and - most importantly - what
is not known.

Two things here are computed rather than asserted.

**Resolution mismatch.** Saying a dataset is "coarse" conveys nothing. Saying that
one NASA POWER cell covers 275,000 hectares while the field is 65 - a ratio of over
four thousand to one - tells a farmer exactly how much to trust a number derived
from it. The ratio is calculated from the actual pixel and field areas.

**Confidence from the weakest link, not an average.** A card resting on one
low-confidence input is a low-confidence card however many high-confidence inputs
sit beside it. Averaging would let good inputs launder a bad one, which is the
specific failure this layer exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .guardrails import Level, Verdict

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
HECTARES_PER_SQ_METRE = 1e-4

# A quarter section, the standard US field unit - used only as a default so the
# mismatch ratio is never silently omitted.
DEFAULT_FIELD_HA = 65.0


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class DataSource:
    """One dataset, what it contributed, and how coarse it is."""

    name: str
    variables: str
    role: str
    period: str
    pixel_metres: float | None  # None for polygon or point data
    confidence: str
    caveat: str = ""

    def __post_init__(self) -> None:
        if self.confidence not in CONFIDENCE_ORDER:
            raise EvidenceError(f"bad confidence {self.confidence!r} for {self.name}")

    @property
    def pixel_hectares(self) -> float | None:
        if self.pixel_metres is None:
            return None
        return (self.pixel_metres**2) * HECTARES_PER_SQ_METRE

    def mismatch_ratio(self, field_ha: float) -> float | None:
        """How many of this field fit inside one pixel."""
        area = self.pixel_hectares
        if area is None or field_ha <= 0:
            return None
        return area / field_ha

    def describe(self, field_ha: float) -> str:
        ratio = self.mismatch_ratio(field_ha)
        if ratio is None:
            scale = "field scale"
        elif ratio < 1.0:
            scale = f"{self.pixel_hectares:.2f} ha pixel, finer than the field"
        else:
            scale = (
                f"{self.pixel_hectares:,.0f} ha pixel - {ratio:,.0f}x the field area"
            )
        line = f"{self.name} ({self.variables}): {self.role}. {self.period}. {scale}"
        return line + (f" -- {self.caveat}" if self.caveat else "")


@dataclass(frozen=True)
class ModelBasis:
    """One equation set, and what checking it showed."""

    name: str
    reference: str
    role: str
    validation: str | None = None
    limitation: str | None = None

    def describe(self) -> str:
        parts = [f"{self.name} ({self.reference}): {self.role}"]
        if self.validation:
            parts.append(f"validated: {self.validation}")
        if self.limitation:
            parts.append(f"limitation: {self.limitation}")
        return ". ".join(parts)


@dataclass(frozen=True)
class EvidenceCard:
    """One auditable recommendation."""

    claim: str
    field_hectares: float
    numbers: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    sources: tuple[DataSource, ...] = field(default_factory=tuple)
    models: tuple[ModelBasis, ...] = field(default_factory=tuple)
    guardrails: tuple[Verdict, ...] = field(default_factory=tuple)
    unknowns: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.field_hectares <= 0:
            raise EvidenceError("field area must be positive")

    @property
    def confidence(self) -> str:
        """The weakest input decides. Averaging would let good inputs hide a bad one."""
        if not self.sources:
            return "low"
        return min(
            (s.confidence for s in self.sources), key=lambda c: CONFIDENCE_ORDER[c]
        )

    @property
    def limiting_sources(self) -> tuple[DataSource, ...]:
        """Every dataset at the lowest confidence - the things to improve first.

        Returns all of them rather than one. Naming a single source when two share
        the floor would imply that fixing it alone would raise the card, which is
        exactly the kind of quiet half-truth this layer exists to avoid.
        """
        if not self.sources:
            return ()
        floor = self.confidence
        return tuple(s for s in self.sources if s.confidence == floor)

    @property
    def coarsest_source(self) -> DataSource | None:
        rated = [s for s in self.sources if s.pixel_metres is not None]
        return max(rated, key=lambda s: s.pixel_metres) if rated else None

    @property
    def refusals(self) -> tuple[Verdict, ...]:
        return tuple(v for v in self.guardrails if v.level is Level.REFUSE)

    @property
    def warnings(self) -> tuple[Verdict, ...]:
        return tuple(v for v in self.guardrails if v.level is Level.WARN)

    @property
    def needs_local_review(self) -> tuple[str, ...]:
        """Everything a person with local knowledge has to settle.

        Assembled rather than written, so it cannot fall out of step with the
        guardrails and sources it summarises.
        """
        items = [
            f"{v.rule}: {v.message}" for v in self.guardrails if v.needs_local_review
        ]
        items += [f"{s.name}: {s.caveat}" for s in self.sources if s.caveat]
        return tuple(items)

    @property
    def is_blocked(self) -> bool:
        return bool(self.refusals)

    def render(self) -> str:
        """A card a farmer or an agronomist can read end to end."""
        lines = [self.claim, "=" * min(len(self.claim), 74)]

        if self.numbers:
            lines.append("")
            for label, value in self.numbers:
                lines.append(f"  {label:38s} {value}")

        if self.is_blocked:
            lines += ["", "NOT RECOMMENDED HERE:"]
            lines += [f"  - {v.message}" for v in self.refusals]

        if self.warnings:
            lines += ["", "Risks, quantified from the observed record:"]
            for v in self.warnings:
                freq = f" ({v.frequency:.0%} of years)" if v.frequency else ""
                lines.append(f"  - {v.message}{freq}")

        lines += ["", "Where the numbers come from:"]
        lines += [f"  - {s.describe(self.field_hectares)}" for s in self.sources]

        lines += ["", "How they were computed:"]
        lines += [f"  - {m.describe()}" for m in self.models]

        if self.unknowns:
            lines += ["", "What this does NOT account for:"]
            lines += [f"  - {u}" for u in self.unknowns]

        review = self.needs_local_review
        if review:
            lines += ["", "Needs someone with local knowledge:"]
            lines += [f"  - {r}" for r in review]

        limiting = self.limiting_sources
        lines += ["", f"Overall confidence: {self.confidence.upper()}"]
        if limiting and self.confidence != "high":
            names = ", ".join(s.name for s in limiting)
            plural = "these inputs" if len(limiting) > 1 else "that input"
            lines.append(
                f"  capped by {names}; improving {plural} would raise it"
            )
        coarsest = self.coarsest_source
        if coarsest is not None:
            ratio = coarsest.mismatch_ratio(self.field_hectares)
            if ratio and ratio > 10:
                lines.append(
                    f"  coarsest input is {coarsest.name} at {ratio:,.0f}x the field "
                    "area, so treat absolute values as regional and prefer the "
                    "comparison between rotations"
                )
        lines += [
            "",
            "FieldShift supports a decision; it does not make one. Nothing here "
            "replaces local agronomic judgement.",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Structured form, for an API or a user interface."""
        return {
            "claim": self.claim,
            "field_hectares": self.field_hectares,
            "confidence": self.confidence,
            "limiting_sources": [s.name for s in self.limiting_sources],
            "blocked": self.is_blocked,
            "numbers": dict(self.numbers),
            "sources": [
                {
                    "name": s.name,
                    "variables": s.variables,
                    "role": s.role,
                    "period": s.period,
                    "pixel_hectares": s.pixel_hectares,
                    "mismatch_ratio": s.mismatch_ratio(self.field_hectares),
                    "confidence": s.confidence,
                    "caveat": s.caveat or None,
                }
                for s in self.sources
            ],
            "models": [
                {
                    "name": m.name, "reference": m.reference, "role": m.role,
                    "validation": m.validation, "limitation": m.limitation,
                }
                for m in self.models
            ],
            "refusals": [v.message for v in self.refusals],
            "warnings": [
                {"rule": v.rule, "message": v.message, "frequency": v.frequency}
                for v in self.warnings
            ],
            "unknowns": list(self.unknowns),
            "needs_local_review": list(self.needs_local_review),
        }


# ---------------------------------------------------------------- the sources used

# Pixel sizes are the datasets' native resolutions. POWER's cell is about
# 0.5 x 0.625 degrees, which near 38 N is roughly 55 km by 50 km - represented here
# by an equivalent square so the field-area ratio is honest rather than flattering.
POWER = DataSource(
    name="NASA POWER",
    variables="temperature, humidity, wind, solar radiation, precipitation",
    role="daily weather driving every water and carbon calculation",
    period="1984-2024, 41 complete years",
    pixel_metres=52_000.0,
    confidence="high",
    caveat="",
)
GRIDMET = DataSource(
    name="gridMET",
    variables="grass reference evapotranspiration",
    role="independent check on our computed reference ET",
    period="1984-2024",
    pixel_metres=4_000.0,
    confidence="high",
)
SSURGO = DataSource(
    name="USDA SSURGO",
    variables="texture, available water capacity, organic matter, bulk density",
    role="soil properties at this field",
    period="current survey",
    pixel_metres=None,
    confidence="high",
    caveat=(
        "awc_r is a representative value for the map unit, not a measurement of "
        "your field; a soil test would narrow it"
    ),
)
NASADEM = DataSource(
    name="NASADEM",
    variables="elevation",
    role="slope and slope length for the erosion estimate",
    period="2000 acquisition",
    pixel_metres=30.0,
    confidence="medium",
    caveat=(
        "on near-flat ground the vertical noise is comparable to the real gradient, "
        "so slope is fitted over 300 m rather than read off adjacent cells"
    ),
)
QUICKSTATS = DataSource(
    name="USDA NASS QuickStats",
    variables="county crop yields by production practice",
    role="validating modelled stress against observed yield",
    period="1984 onwards",
    pixel_metres=None,
    confidence="high",
    caveat="county averages, so they bound the model rather than describe this field",
)
CROP_TABLE = DataSource(
    name="FieldShift crop table",
    variables="FAO-56 coefficients, rooting depth, nitrogen, residue, Ky",
    role="crop behaviour",
    period="literature values",
    pixel_metres=None,
    confidence="medium",
    caveat=(
        "one cultivar per crop, and a short-season hybrid can behave quite "
        "differently; pathogen return intervals are literature mid-ranges that can "
        "be out by a factor of two locally"
    ),
)

# ---------------------------------------------------------------- the models used

MODELS = {
    "et0": ModelBasis(
        "FAO-56 Penman-Monteith", "Allen et al. 1998, eq 6",
        "reference evapotranspiration",
        validation="-3.2% against gridMET over 40 years, daily r = 0.966",
    ),
    "water": ModelBasis(
        "FAO-56 dual crop coefficient", "Allen et al. 1998, ch 7-8",
        "separates transpiration from soil evaporation",
        validation=(
            "stress index vs detrended county yield: 11 of 11 dryland "
            "crop-site combinations positive, 8 significant, mean r = +0.45"
        ),
        limitation="flooded rice needs a percolation term this does not provide",
    ),
    "carbon": ModelBasis(
        "RothC-26.3", "Coleman & Jenkinson",
        "soil organic carbon turnover, 0-23 cm",
        validation="0.38 t C/ha/yr for irrigated no-till, inside the 0.2-0.5 reported",
        limitation="describes the top 23 cm only",
    ),
    "nitrogen": ModelBasis(
        "nitrogen balance coupled to RothC flows", "SUNDIAL-style C:N coupling",
        "mineralisation, immobilisation, fixation, leaching",
        limitation=(
            "denitrification is a flat monthly fraction, because its real control is "
            "anaerobic microsites a field-scale model cannot resolve"
        ),
    ),
    "erosion": ModelBasis(
        "RUSLE", "Renard et al.; Williams 1995 for erodibility",
        "water erosion",
        limitation=(
            "WATER erosion only, and the R factor is a rainfall regression rather "
            "than measured 30-minute intensity"
        ),
    ),
    "rotation": ModelBasis(
        "FieldShift sequence simulator", "this project",
        "carries water, nitrogen, carbon and residue between crops",
        validation=(
            "reproduces an observed rotation effect: USDA reports wheat after "
            "summer fallow out-yielding continuous wheat by 10.8% over 25 years; "
            "the model gives 18.0% with nothing fitted to it"
        ),
        limitation=(
            "over-predicts the fallow benefit by 4-7 points; a sensitivity sweep "
            "shows parameter error cannot explain it, so the cause is structural - "
            "a modelled fallow is perfectly bare while a real one loses water to weeds"
        ),
    ),
}

STANDING_UNKNOWNS = (
    "wind erosion, which is the dominant mechanism where the bare season is also "
    "the dry season - quantifying it needs field roughness data no available "
    "dataset carries",
    "local market access, prices, seed availability and contracts",
    "your equipment, labour and storage constraints",
    "whether the soilborne organisms behind the rotation intervals are actually "
    "present in this field",
    "sowing dates, which are set locally rather than by the model",
    "groundwater decline, because the USGS service that carried historical well "
    "levels has been retired and its replacement does not expose them",
)


def build_card(
    claim: str,
    *,
    field_hectares: float = DEFAULT_FIELD_HA,
    numbers: Sequence[tuple[str, str]] = (),
    sources: Sequence[DataSource] = (POWER, SSURGO, CROP_TABLE),
    model_keys: Sequence[str] = ("et0", "water", "carbon", "rotation"),
    guardrails: Iterable[Verdict] = (),
    extra_unknowns: Sequence[str] = (),
) -> EvidenceCard:
    """Assemble a card. Standing unknowns are always included."""
    unknown_keys = [k for k in model_keys if k not in MODELS]
    if unknown_keys:
        raise EvidenceError(f"unknown model keys: {unknown_keys}")
    return EvidenceCard(
        claim=claim,
        field_hectares=field_hectares,
        numbers=tuple(numbers),
        sources=tuple(sources),
        models=tuple(MODELS[k] for k in model_keys),
        guardrails=tuple(guardrails),
        unknowns=tuple(extra_unknowns) + STANDING_UNKNOWNS,
    )
