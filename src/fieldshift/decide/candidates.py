"""Generating candidate rotations, and explaining what was rejected.

A rotation is built from **year patterns** rather than from free-form crop slots.
A pattern is one year's cropping that is already known to fit the local calendar -
winter wheat sown in October and harvested in July, a summer crop from May to
October, a fallow year, wheat followed by a cover crop. Searching over patterns
instead of crops means the generator cannot produce sequences where two crops
occupy the field at once, which was the single most common way a hand-written
rotation turned out to be impossible.

Two things here matter as much as the search itself.

**Cyclic equivalence.** A rotation repeats, so [wheat, fallow, sorghum] and
[fallow, sorghum, wheat] are the same rotation started at a different point.
Enumerating both would triple the apparent search space and triple the compute, and
would present a farmer with duplicates dressed as alternatives. Candidates are
reduced to a canonical rotation of the cycle.

**Rejections are counted and explained.** "We examined 1,364 rotations and rejected
1,201" is only useful if the reasons are visible, and a constraint that silently
eliminates every interesting option is a bug that looks like a result.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from itertools import product
from typing import Iterable, Sequence

from ..agro.rotation import RotationPlan, SeasonPlan
from ..data.registry import Registry


class CandidateError(ValueError):
    pass


@dataclass(frozen=True)
class YearPattern:
    """One year's cropping, already known to fit the local calendar."""

    id: str
    name: str
    seasons: tuple[SeasonPlan, ...]
    notes: str = ""

    def __post_init__(self) -> None:
        if any(s.year_offset != 0 for s in self.seasons):
            raise CandidateError(
                f"{self.id}: a year pattern describes one year, so every season must "
                "have year_offset 0; the offset is assigned when the rotation is built"
            )

    @property
    def crop_ids(self) -> tuple[str, ...]:
        return tuple(s.crop_id for s in self.seasons)

    @property
    def is_fallow_year(self) -> bool:
        """A year with no crop at all. Distinct from a year with a short bare gap."""
        return not self.seasons

    @property
    def is_irrigated(self) -> bool:
        return any(s.irrigation.is_irrigated for s in self.seasons)


@dataclass(frozen=True)
class RotationConstraints:
    """Hard limits a candidate must satisfy to be worth simulating.

    These are feasibility filters, not preferences. Preferences belong in the
    objective ranking, where a farmer can trade them off; a constraint here removes
    an option entirely, so each one needs to be defensible as impossible rather
    than merely undesirable.
    """

    max_consecutive_fallow_years: int = 1
    max_fallow_fraction: float = 0.6
    require_cash_crop: bool = True
    min_distinct_crops: int = 1
    max_irrigated_years: int | None = None
    forbid_crops: frozenset[str] = frozenset()
    confirmed_pathogens: frozenset[str] = frozenset()
    enforce_return_intervals: bool = True

    def __post_init__(self) -> None:
        if self.max_consecutive_fallow_years < 0:
            raise CandidateError("max_consecutive_fallow_years cannot be negative")
        if not 0.0 <= self.max_fallow_fraction <= 1.0:
            raise CandidateError("max_fallow_fraction must be in [0, 1]")
        if self.min_distinct_crops < 0:
            raise CandidateError("min_distinct_crops cannot be negative")


@dataclass(frozen=True)
class Rejection:
    """Why one candidate was discarded."""

    sequence: tuple[str, ...]
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class CandidateSet:
    """Feasible rotations, plus an account of what was removed and why."""

    plans: tuple[RotationPlan, ...]
    examined: int
    rejections: tuple[Rejection, ...] = field(default_factory=tuple)

    def __len__(self) -> int:
        return len(self.plans)

    @property
    def rejection_tally(self) -> dict[str, int]:
        return dict(Counter(r.reason for r in self.rejections))

    def describe(self) -> str:
        lines = [
            f"{len(self.plans)} feasible rotations from {self.examined} distinct "
            f"cycles examined"
        ]
        for reason, count in sorted(
            self.rejection_tally.items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"  rejected {count:5d}  {reason}")
        if not self.plans:
            lines.append(
                "  NOTHING SURVIVED. A constraint that eliminates every option is a "
                "bug that looks like a result - relax the tightest filter above."
            )
        return "\n".join(lines)


# ---------------------------------------------------------------- cyclic identity


def canonical_cycle(sequence: Sequence[str]) -> tuple[str, ...]:
    """The lexicographically smallest rotation of a cyclic sequence.

    Rotations repeat, so a cycle has no inherent starting point. Reducing to a
    canonical form is what stops [A,B,C], [B,C,A] and [C,A,B] being counted - and
    simulated, and shown to a farmer - as three different options.
    """
    if not sequence:
        return ()
    items = tuple(sequence)
    n = len(items)
    return min(tuple(items[i:] + items[:i]) for i in range(n))


# ---------------------------------------------------------------- feasibility


def _slot_sequence(patterns: Sequence[YearPattern]) -> list[str]:
    """Crop ids in calendar order across the cycle, for the interval check."""
    out: list[str] = []
    for pattern in patterns:
        for season in sorted(pattern.seasons, key=lambda s: s.planting_doy):
            out.append(season.crop_id)
    return out


def _occupied_intervals(
    patterns: Sequence[YearPattern], registry: Registry
) -> list[tuple[float, float, str]]:
    """Day-of-cycle intervals each crop occupies, for the overlap check."""
    out = []
    for offset, pattern in enumerate(patterns):
        for season in pattern.seasons:
            duration = season.duration_days or registry.crop(season.crop_id).duration_days
            start = offset * 365.25 + season.planting_doy
            out.append((start, start + duration, season.crop_id))
    return sorted(out)


def check_calendar(
    patterns: Sequence[YearPattern], registry: Registry
) -> Rejection | None:
    """Reject sequences where two crops would occupy the field at once.

    Checked around the cycle, because an overwintering crop sown in October of the
    last year runs into the first year of the next pass. Catching this here rather
    than at simulation time means an impossible calendar is reported as a reason
    rather than an exception, and costs nothing to evaluate.
    """
    intervals = _occupied_intervals(patterns, registry)
    ids = tuple(p.id for p in patterns)
    cycle_days = len(patterns) * 365.25

    for (a_start, a_end, a_crop), (b_start, b_end, b_crop) in zip(
        intervals, intervals[1:]
    ):
        if a_end > b_start:
            return Rejection(
                ids, "impossible calendar",
                f"{a_crop} runs to day {a_end:.0f} but {b_crop} is sown on day "
                f"{b_start:.0f}",
            )
    if intervals:
        first_start, _, first_crop = intervals[0]
        _, last_end, last_crop = intervals[-1]
        if last_end > first_start + cycle_days:
            return Rejection(
                ids, "impossible calendar",
                f"{last_crop} overruns into the next cycle, where {first_crop} is "
                "already sown",
            )
    return None


def check_feasible(
    patterns: Sequence[YearPattern],
    registry: Registry,
    constraints: RotationConstraints,
) -> Rejection | None:
    """Return a Rejection if the candidate is infeasible, else None."""
    ids = tuple(p.id for p in patterns)
    crops = _slot_sequence(patterns)

    calendar_problem = check_calendar(patterns, registry)
    if calendar_problem is not None:
        return calendar_problem

    forbidden = set(crops) & set(constraints.forbid_crops)
    if forbidden:
        return Rejection(ids, "contains a forbidden crop", ", ".join(sorted(forbidden)))

    fallow_years = sum(1 for p in patterns if p.is_fallow_year)
    if fallow_years / len(patterns) > constraints.max_fallow_fraction + 1e-9:
        return Rejection(
            ids, "too much fallow",
            f"{fallow_years}/{len(patterns)} years exceeds "
            f"{constraints.max_fallow_fraction:.0%}",
        )

    # Consecutive fallow is checked around the cycle, since the rotation repeats.
    flags = [p.is_fallow_year for p in patterns]
    doubled = flags + flags
    run = best = 0
    for flag in doubled:
        run = run + 1 if flag else 0
        best = max(best, run)
    best = min(best, len(patterns))
    if best > constraints.max_consecutive_fallow_years:
        return Rejection(
            ids, "too many consecutive fallow years",
            f"{best} in a row exceeds {constraints.max_consecutive_fallow_years}",
        )

    if constraints.require_cash_crop:
        marketed = [
            c for c in crops if registry.crop(c).economics.yield_typical > 0
        ]
        if not marketed:
            return Rejection(ids, "no marketable crop", "a farmer needs an income")

    distinct = {c for c in crops}
    if len(distinct) < constraints.min_distinct_crops:
        return Rejection(
            ids, "not diverse enough",
            f"{len(distinct)} distinct crops, need {constraints.min_distinct_crops}",
        )

    if constraints.max_irrigated_years is not None:
        irrigated = sum(1 for p in patterns if p.is_irrigated)
        if irrigated > constraints.max_irrigated_years:
            return Rejection(
                ids, "exceeds the irrigation limit",
                f"{irrigated} irrigated years allowed {constraints.max_irrigated_years}",
            )

    if constraints.enforce_return_intervals and crops:
        slots_per_year = max(1, round(len(crops) / len(patterns)))
        violations = registry.check_rotation(
            crops,
            slots_per_year=slots_per_year,
            cyclic=True,
            confirmed_present=sorted(constraints.confirmed_pathogens),
        )
        blocking = [v for v in violations if v.is_blocking]
        if blocking:
            worst = blocking[0]
            return Rejection(
                ids, "breaks a pathogen return interval",
                f"{worst.crop_b} returns after {worst.gap_years:g} yr; "
                f"{worst.driver} needs {worst.required_years}",
            )
    return None


# ---------------------------------------------------------------- generation


def build_rotation(
    patterns: Sequence[YearPattern], name: str | None = None
) -> RotationPlan:
    """Turn a sequence of year patterns into a simulable RotationPlan."""
    if not patterns:
        raise CandidateError("a rotation needs at least one year")
    seasons: list[SeasonPlan] = []
    for offset, pattern in enumerate(patterns):
        for season in pattern.seasons:
            seasons.append(replace(season, year_offset=offset))
    if not seasons:
        raise CandidateError(
            "every year in this rotation is fallow; there is nothing to simulate"
        )
    label = name or " - ".join(p.name for p in patterns)
    return RotationPlan(
        name=label,
        seasons=tuple(seasons),
        cycle_years=len(patterns),
        notes="; ".join(p.notes for p in patterns if p.notes),
    )


def enumerate_rotations(
    patterns: Sequence[YearPattern],
    *,
    registry: Registry,
    cycle_years: int,
    constraints: RotationConstraints | None = None,
    max_candidates: int | None = 5000,
) -> CandidateSet:
    """Enumerate distinct feasible rotations of a given cycle length.

    Exhaustive over year patterns, with cyclic duplicates removed before any
    simulation runs. Exhaustive search is the right choice at this scale: a handful
    of patterns over a four- to six-year cycle is a few thousand candidates once
    duplicates are gone, each cheap to filter, and an exact enumeration is far
    easier to defend than a heuristic that might have missed the good answer.
    """
    if cycle_years < 1:
        raise CandidateError("cycle_years must be at least 1")
    if not patterns:
        raise CandidateError("no year patterns supplied")
    ids = [p.id for p in patterns]
    if len(set(ids)) != len(ids):
        raise CandidateError("year pattern ids must be unique")

    constraints = constraints or RotationConstraints()
    by_id = {p.id: p for p in patterns}

    seen: set[tuple[str, ...]] = set()
    plans: list[RotationPlan] = []
    rejections: list[Rejection] = []
    examined = 0

    for combo in product(ids, repeat=cycle_years):
        canonical = canonical_cycle(combo)
        if canonical in seen:
            continue
        seen.add(canonical)
        examined += 1

        chosen = [by_id[i] for i in canonical]
        if all(p.is_fallow_year for p in chosen):
            rejections.append(
                Rejection(canonical, "no marketable crop", "every year is fallow")
            )
            continue

        problem = check_feasible(chosen, registry, constraints)
        if problem is not None:
            rejections.append(problem)
            continue

        plans.append(build_rotation(chosen))
        if max_candidates is not None and len(plans) >= max_candidates:
            break

    return CandidateSet(
        plans=tuple(plans), examined=examined, rejections=tuple(rejections)
    )


def search_space_size(n_patterns: int, cycle_years: int) -> tuple[int, int]:
    """(raw combinations, distinct cycles) - how much cyclic dedup actually saves."""
    if n_patterns < 1 or cycle_years < 1:
        raise CandidateError("both arguments must be positive")
    raw = n_patterns**cycle_years
    distinct = len(
        {canonical_cycle(c) for c in product(range(n_patterns), repeat=cycle_years)}
    )
    return raw, distinct
