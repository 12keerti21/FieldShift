"""Loading, validation and derived queries over the parameter tables.

Nothing here does agronomy arithmetic beyond the return-interval constraint; the
physical models live in fieldshift.agro. The point of this module is that every number
the engine uses enters through one validated door.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Iterable, Sequence

from .soils import VALID_HYDROLOGIC_GROUPS, SoilTexture, classify_texture
from .schema import (
    Crop,
    Economics,
    IntervalConstraint,
    Kc,
    Meta,
    Nitrogen,
    Pathogen,
    Residue,
    Root,
    RotationInfo,
    Stages,
    Thermal,
    Traits,
    Violation,
    Water,
    YieldResponse,
)

VALID_DPM_RPM = {1.44, 0.67, 0.25}  # RothC: arable/improved grass/woodland
VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_EFFICACY = {"strong", "partial", "weak", "none"}
VALID_PRESENCE = {"widespread", "regional", "quarantine"}


class ParameterError(ValueError):
    """A parameter table failed validation. Fail loudly: silent bad agronomy is worse."""


def _read(name: str) -> dict:
    with resources.files("fieldshift.data").joinpath(name).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _crop(d: dict) -> Crop:
    return Crop(
        id=d["id"],
        name=d["name"],
        family=d["family"],
        group=d["group"],
        duration_days=int(d["duration_days"]),
        multi_year=bool(d["multi_year"]),
        typical_seasons=tuple(d["typical_seasons"]),
        kc=Kc(**d["kc"]),
        kcb=Kc(**d["kcb"]),
        stages_days=Stages(**d["stages_days"]),
        root=Root(**d["root"]),
        thermal=Thermal(**d["thermal"]),
        water=Water(seasonal_req_mm=tuple(d["water"]["seasonal_req_mm"])),
        nitrogen=Nitrogen(
            is_fixer=bool(d["nitrogen"]["is_fixer"]),
            bnf_kg_n_ha=tuple(d["nitrogen"]["bnf_kg_n_ha"]),
            ndfa_fraction=d["nitrogen"]["ndfa_fraction"],
            n_uptake_kg_per_t_product=d["nitrogen"]["n_uptake_kg_per_t_product"],
        ),
        residue=Residue(**d["residue"]),
        traits=Traits(**d["traits"]),
        rotation=RotationInfo(**d["rotation"]),
        economics=Economics(
            yield_t_ha=tuple(d["economics"]["yield_t_ha"]),
            price_local_required=bool(d["economics"]["price_local_required"]),
        ),
        ky=YieldResponse(
            value=d["ky"]["value"],
            confidence=d["ky"]["confidence"],
            source=d["ky"]["source"],
        ),
        meta=Meta(confidence=d["meta"]["confidence"], sources=tuple(d["meta"]["sources"])),
    )


def _texture(d: dict) -> SoilTexture:
    return SoilTexture(**{k: v for k, v in d.items()})


def _pathogen(d: dict) -> Pathogen:
    return Pathogen(
        id=d["id"],
        name=d["name"],
        organism=d["organism"],
        type=d["type"],
        inoculum=d["inoculum"],
        host_crops=tuple(d["host_crops"]),
        host_families=tuple(d["host_families"]),
        soil_persistence_years=int(d["soil_persistence_years"]),
        recommended_return_interval_years=int(d["recommended_return_interval_years"]),
        rotation_effective=bool(d["rotation_effective"]),
        rotation_efficacy=d["rotation_efficacy"],
        presence=d["presence"],
        notes=d["notes"],
        confidence=d["confidence"],
    )


@dataclass(frozen=True)
class Registry:
    crops: dict[str, Crop]
    pathogens: dict[str, Pathogen]
    textures: dict[str, SoilTexture]
    crop_notes: dict  # provenance / known_gaps blocks, surfaced in evidence cards
    pathogen_notes: dict
    soil_notes: dict
    c_fraction_of_dry_matter: float

    # ---------- lookups ----------

    def crop(self, crop_id: str) -> Crop:
        try:
            return self.crops[crop_id]
        except KeyError:
            raise ParameterError(f"unknown crop id {crop_id!r}") from None

    def pathogen(self, pathogen_id: str) -> Pathogen:
        try:
            return self.pathogens[pathogen_id]
        except KeyError:
            raise ParameterError(f"unknown pathogen id {pathogen_id!r}") from None

    def texture(self, texture_id: str) -> SoilTexture:
        try:
            return self.textures[texture_id]
        except KeyError:
            raise ParameterError(f"unknown soil texture id {texture_id!r}") from None

    def texture_from_fractions(
        self, sand_pct: float, silt_pct: float, clay_pct: float
    ) -> SoilTexture:
        """Classify measured fractions, then keep them rather than the centroid.

        This is the path gridded soil products take: SoilGrids and similar give
        fractions, not class names.
        """
        from .soils import texture_with_measured_fractions

        try:
            tid = classify_texture(sand_pct, silt_pct, clay_pct)
        except ValueError as exc:
            raise ParameterError(str(exc)) from None
        return texture_with_measured_fractions(
            self.texture(tid), sand_pct, silt_pct, clay_pct
        )

    def crops_in_family(self, family: str) -> list[Crop]:
        return [c for c in self.crops.values() if c.family == family]

    def selectable_crops(self) -> list[Crop]:
        """Everything the optimiser may place in a slot, bare fallow included.

        Fallow is selectable on purpose: the baseline rotation usually contains one,
        and we cannot cost a bare window without being able to represent it.
        """
        return list(self.crops.values())

    # ---------- pathogen / interval derivation ----------

    def hosts(self, pathogen: Pathogen) -> set[str]:
        """Susceptible crop ids, expanding family-level host ranges."""
        out = set(pathogen.host_crops)
        for fam in pathogen.host_families:
            out.update(c.id for c in self.crops_in_family(fam))
        return out

    def pathogens_for(self, crop_id: str, rotation_effective_only: bool = True) -> list[Pathogen]:
        self.crop(crop_id)  # validate
        found = []
        for p in self.pathogens.values():
            if rotation_effective_only and not p.rotation_effective:
                continue
            if crop_id in self.hosts(p):
                found.append(p)
        # id is the tie-break: equal intervals must not reorder between runs, or the
        # "driving" pathogen shown to a farmer would change at random.
        return sorted(found, key=lambda p: (-p.recommended_return_interval_years, p.id))

    def shared_pathogens(
        self, crop_a: str, crop_b: str, rotation_effective_only: bool = True
    ) -> list[Pathogen]:
        """Pathogens hosted by both crops.

        This is the real test of whether a rotation breaks a disease cycle. Two
        different crops that share a broad-host organism provide much less of a break
        than their taxonomic distance suggests.
        """
        a = {p.id for p in self.pathogens_for(crop_a, rotation_effective_only)}
        b = {p.id for p in self.pathogens_for(crop_b, rotation_effective_only)}
        return sorted(
            (self.pathogens[i] for i in a & b),
            key=lambda p: (-p.recommended_return_interval_years, p.id),
        )

    def min_return_interval(self, crop_id: str) -> IntervalConstraint:
        """Years before this crop may return, and what drives the limit.

        Takes the strictest of the pathogen-derived intervals and the agronomic one
        (allelopathy, nutrient depletion), ignoring pathogens rotation cannot control.
        """
        crop = self.crop(crop_id)
        agronomic = crop.rotation.min_return_interval_agronomic_years
        best = IntervalConstraint(
            crop_id=crop_id,
            years=agronomic,
            driver="agronomic practice",
            driver_kind="agronomic",
            efficacy="partial",
            confidence=crop.meta.confidence,
            presence="widespread",
            pathogen_id=None,
            explanation=crop.rotation.notes,
        )
        for p in self.pathogens_for(crop_id):  # already deterministically ordered
            if p.recommended_return_interval_years > best.years:
                best = IntervalConstraint(
                    crop_id=crop_id,
                    years=p.recommended_return_interval_years,
                    driver=p.name,
                    driver_kind="pathogen",
                    efficacy=p.rotation_efficacy,
                    confidence=p.confidence,
                    presence=p.presence,
                    pathogen_id=p.id,
                    explanation=p.notes,
                )
        return best

    # ---------- rotation checking ----------

    def check_rotation(
        self,
        sequence: Sequence[str],
        slots_per_year: int = 1,
        cyclic: bool = True,
        confirmed_present: Iterable[str] | None = None,
    ) -> list[Violation]:
        """Flag return-interval breaches in a crop sequence.

        ``sequence`` is slots in chronological order. ``slots_per_year`` handles
        multi-season systems (2 for a kharif/rabi double crop), so the gap between
        slots is converted to years before being compared with an interval.

        ``cyclic`` is on by default because a rotation repeats: the last slot is
        followed by the first one again, and a sequence that looks clean read
        left-to-right can still put a crop back on the field too soon once it loops.
        Ignoring the wrap is the most common way a rotation checker gives a false pass.

        ``confirmed_present`` are pathogen ids a local source has actually reported
        for this field or district. Patchily distributed organisms stay advisory
        until they appear here - see Violation.severity.

        One violation is returned per conflicting crop pair, reporting the tightest
        spacing in the cycle. A four-slot rotation can pair the same two crops many
        times over; listing each one buries the finding that matters.
        """
        if slots_per_year < 1:
            raise ParameterError("slots_per_year must be >= 1")
        for cid in sequence:
            self.crop(cid)
        confirmed = set(confirmed_present or ())
        for pid in confirmed:
            self.pathogen(pid)  # validate

        n = len(sequence)
        binding: dict[tuple[str, str], Violation] = {}

        for i in range(n):
            for j in range(i + 1, n):
                for wrapped, gap_slots in self._gaps(i, j, n, cyclic):
                    v = self._conflict(
                        sequence, i, j, gap_slots / slots_per_year, wrapped, confirmed
                    )
                    if v is None:
                        continue
                    key = tuple(sorted((v.crop_a, v.crop_b)))
                    held = binding.get(key)
                    if held is None or (v.gap_years, v.wrapped) < (
                        held.gap_years,
                        held.wrapped,
                    ):
                        binding[key] = v

        return sorted(
            binding.values(),
            key=lambda v: (-v.required_years, v.gap_years, v.slot_a, v.slot_b),
        )

    @staticmethod
    def _gaps(i: int, j: int, n: int, cyclic: bool) -> Iterable[tuple[bool, int]]:
        yield False, j - i
        if cyclic:
            yield True, n - (j - i)

    def _conflict(
        self,
        sequence: Sequence[str],
        i: int,
        j: int,
        gap_years: float,
        wrapped: bool,
        confirmed: set[str],
    ) -> Violation | None:
        crop_a, crop_b = sequence[i], sequence[j]
        if self.crop(crop_a).is_fallow or self.crop(crop_b).is_fallow:
            return None

        if crop_a == crop_b:
            c = self.min_return_interval(crop_a)
            required, driver = c.years, c.driver
            kind, efficacy, conf, presence = (
                c.driver_kind,
                c.efficacy,
                c.confidence,
                c.presence,
            )
            pathogen_id = c.pathogen_id
        else:
            shared = self.shared_pathogens(crop_a, crop_b)
            if not shared:
                return None
            p = shared[0]
            required, driver = p.recommended_return_interval_years, p.name
            kind, efficacy, conf, presence = (
                "pathogen",
                p.rotation_efficacy,
                p.confidence,
                p.presence,
            )
            pathogen_id = p.id

        if gap_years >= required:
            return None

        return Violation(
            slot_a=i,
            slot_b=j,
            crop_a=crop_a,
            crop_b=crop_b,
            gap_years=gap_years,
            required_years=required,
            driver=driver,
            driver_kind=kind,
            efficacy=efficacy,
            confidence=conf,
            presence=presence,
            presence_confirmed=pathogen_id in confirmed if pathogen_id else False,
            wrapped=wrapped,
        )

    def rotation_is_feasible(self, sequence: Sequence[str], **kw) -> bool:
        """True when nothing in the sequence is a blocking breach.

        Only 'high' severity blocks. 'moderate' and 'caution' breaches are reported
        and left to the farmer: rejecting them would eliminate rotations that are
        already run successfully, and the tool's job is to inform that judgement,
        not to overrule it.
        """
        return not any(v.is_blocking for v in self.check_rotation(sequence, **kw))


# ---------------------------------------------------------------- validation


def _validate(reg: Registry) -> None:
    errs: list[str] = []

    for c in reg.crops.values():
        tag = f"crop {c.id}"
        if not c.is_fallow and c.stages_days.total != c.duration_days:
            errs.append(
                f"{tag}: stage lengths sum to {c.stages_days.total} "
                f"but duration_days is {c.duration_days}"
            )
        for label, v in (("ini", c.kc.ini), ("mid", c.kc.mid), ("end", c.kc.end)):
            if not 0.0 <= v <= 1.4:
                errs.append(f"{tag}: kc.{label}={v} outside plausible 0-1.4")
        for label, v in (("ini", c.kcb.ini), ("mid", c.kcb.mid), ("end", c.kcb.end)):
            if not 0.0 <= v <= 1.4:
                errs.append(f"{tag}: kcb.{label}={v} outside plausible 0-1.4")
        # The basal coefficient excludes soil evaporation, so it can never exceed
        # the single coefficient. This guard is what would have caught the earlier
        # error of using Table 12 values as basal coefficients.
        for label, b, single in (
            ("ini", c.kcb.ini, c.kc.ini),
            ("mid", c.kcb.mid, c.kc.mid),
            ("end", c.kcb.end, c.kc.end),
        ):
            if b > single + 1e-9:
                errs.append(
                    f"{tag}: kcb.{label}={b} exceeds kc.{label}={single}; the basal "
                    "coefficient cannot exceed the single coefficient"
                )
        if not c.is_fallow and not 0.15 <= c.root.p_depletion <= 0.80:
            errs.append(f"{tag}: p_depletion={c.root.p_depletion} outside FAO-56 range")
        if c.root.initial_depth_m > c.root.max_depth_m:
            errs.append(f"{tag}: initial root depth exceeds max")
        if c.thermal.t_base_c >= c.thermal.t_opt_min_c and not c.is_fallow:
            errs.append(f"{tag}: t_base_c >= t_opt_min_c")
        if not c.thermal.t_opt_min_c <= c.thermal.t_opt_max_c <= c.thermal.t_max_c:
            errs.append(f"{tag}: thermal thresholds out of order")
        fixer_flags = {
            c.nitrogen.is_fixer,
            c.nitrogen.bnf_kg_n_ha[1] > 0,
            c.nitrogen.ndfa_fraction > 0,
        }
        if len(fixer_flags) != 1:
            errs.append(f"{tag}: N-fixation flags inconsistent ({c.nitrogen})")
        if c.nitrogen.is_fixer and c.family != "Fabaceae":
            errs.append(f"{tag}: flagged N fixer but family is {c.family}")
        if c.residue.dpm_rpm_ratio not in VALID_DPM_RPM:
            errs.append(f"{tag}: dpm_rpm_ratio={c.residue.dpm_rpm_ratio} not a RothC value")
        if not 0.0 <= c.traits.max_canopy_cover_frac <= 1.0:
            errs.append(f"{tag}: max_canopy_cover_frac out of range")
        for label, v in (
            ("drought", c.traits.drought_tolerance),
            ("heat", c.traits.heat_tolerance),
            ("waterlogging", c.traits.waterlogging_tolerance),
            ("forage", c.traits.forage_value),
        ):
            if not 0 <= v <= 5:
                errs.append(f"{tag}: {label} tolerance {v} outside 0-5")
        if c.ky.confidence not in VALID_CONFIDENCE:
            errs.append(f"{tag}: ky confidence {c.ky.confidence!r} invalid")
        if c.ky.value is not None and not 0.2 <= c.ky.value <= 2.0:
            errs.append(f"{tag}: ky {c.ky.value} outside the plausible 0.2-2.0 range")
        # A crop with a marketable yield needs a Ky, and one without must not have one.
        has_yield = c.economics.yield_typical > 0
        if has_yield and not c.ky.is_known:
            errs.append(f"{tag}: has a marketable yield but no ky")
        if not has_yield and c.ky.is_known:
            errs.append(f"{tag}: has no marketable yield but declares ky {c.ky.value}")
        if c.meta.confidence not in VALID_CONFIDENCE:
            errs.append(f"{tag}: confidence {c.meta.confidence!r} invalid")
        if not c.meta.sources:
            errs.append(f"{tag}: no sources recorded")
        if c.water.seasonal_req_mm[0] > c.water.seasonal_req_mm[1]:
            errs.append(f"{tag}: seasonal water range inverted")
        if not (
            c.economics.yield_t_ha[0] <= c.economics.yield_t_ha[1] <= c.economics.yield_t_ha[2]
        ):
            errs.append(f"{tag}: yield range not ascending")

    known_families = {c.family for c in reg.crops.values()}
    for p in reg.pathogens.values():
        tag = f"pathogen {p.id}"
        for host in p.host_crops:
            if host not in reg.crops:
                errs.append(f"{tag}: host crop {host!r} not in crops table")
        for fam in p.host_families:
            if fam not in known_families:
                errs.append(f"{tag}: host family {fam!r} matches no crop")
        if p.rotation_efficacy not in VALID_EFFICACY:
            errs.append(f"{tag}: efficacy {p.rotation_efficacy!r} invalid")
        if p.confidence not in VALID_CONFIDENCE:
            errs.append(f"{tag}: confidence {p.confidence!r} invalid")
        if p.presence not in VALID_PRESENCE:
            errs.append(f"{tag}: presence {p.presence!r} invalid")
        if p.recommended_return_interval_years > p.soil_persistence_years + 1:
            errs.append(
                f"{tag}: recommended interval {p.recommended_return_interval_years} "
                f"exceeds persistence {p.soil_persistence_years}"
            )
        if not p.rotation_effective and p.rotation_efficacy != "none":
            errs.append(f"{tag}: rotation_effective is false but efficacy is not 'none'")
        if not reg.hosts(p):
            errs.append(f"{tag}: no hosts resolve")

    for s in reg.textures.values():
        tag = f"texture {s.id}"
        total = s.sand_pct + s.silt_pct + s.clay_pct
        if abs(total - 100.0) > 1.0:
            errs.append(f"{tag}: fractions sum to {total:g}, expected 100")
        if not s.theta_wp < s.theta_fc < s.theta_sat:
            errs.append(
                f"{tag}: water contents out of order "
                f"(wp={s.theta_wp} fc={s.theta_fc} sat={s.theta_sat})"
            )
        derived_taw = 1000.0 * (s.theta_fc - s.theta_wp)
        if not 40.0 <= derived_taw <= 220.0:
            errs.append(f"{tag}: TAW {derived_taw:.0f} mm/m outside plausible range")
        if s.measured_awc_mm_per_m is not None and not 20.0 <= s.measured_awc_mm_per_m <= 350.0:
            errs.append(
                f"{tag}: measured AWC {s.measured_awc_mm_per_m:.0f} mm/m implausible"
            )
        if not 2.0 <= s.rew_mm <= 12.0:
            errs.append(f"{tag}: REW {s.rew_mm} outside FAO-56 Table 19 range")
        if s.ksat_mm_h <= 0:
            errs.append(f"{tag}: non-positive Ksat")
        if not 1.0 <= s.bulk_density_g_cm3 <= 1.8:
            errs.append(f"{tag}: implausible bulk density {s.bulk_density_g_cm3}")
        if s.hydrologic_group not in VALID_HYDROLOGIC_GROUPS:
            errs.append(f"{tag}: hydrologic group {s.hydrologic_group!r} invalid")
        if s.confidence not in VALID_CONFIDENCE:
            errs.append(f"{tag}: confidence {s.confidence!r} invalid")
        # A centroid that does not fall in its own class polygon means either the
        # centroid or the classifier is wrong; both are silent, serious errors.
        classified = classify_texture(s.sand_pct, s.silt_pct, s.clay_pct)
        if classified != s.id:
            errs.append(f"{tag}: centroid classifies as {classified!r}, not its own class")

    if errs:
        raise ParameterError("parameter table validation failed:\n  - " + "\n  - ".join(errs))


@lru_cache(maxsize=1)
def load_registry() -> Registry:
    """The validated parameter tables. Cached; raises ParameterError on bad data."""
    crops_doc = _read("crops.json")
    path_doc = _read("pathogens.json")
    soil_doc = _read("soils.json")

    crops = {}
    for d in crops_doc["crops"]:
        if d["id"] in crops:
            raise ParameterError(f"duplicate crop id {d['id']!r}")
        crops[d["id"]] = _crop(d)

    pathogens = {}
    for d in path_doc["pathogens"]:
        if d["id"] in pathogens:
            raise ParameterError(f"duplicate pathogen id {d['id']!r}")
        pathogens[d["id"]] = _pathogen(d)

    textures = {}
    for d in soil_doc["textures"]:
        if d["id"] in textures:
            raise ParameterError(f"duplicate texture id {d['id']!r}")
        textures[d["id"]] = _texture(d)

    reg = Registry(
        crops=crops,
        pathogens=pathogens,
        textures=textures,
        crop_notes={
            "provenance": crops_doc["provenance"],
            "known_gaps": crops_doc["known_gaps"],
        },
        pathogen_notes={
            "provenance": path_doc["provenance"],
            "known_gaps": path_doc["known_gaps"],
        },
        soil_notes={
            "provenance": soil_doc["provenance"],
            "known_gaps": soil_doc["known_gaps"],
        },
        c_fraction_of_dry_matter=crops_doc["constants"]["c_fraction_of_dry_matter"],
    )
    _validate(reg)
    return reg
