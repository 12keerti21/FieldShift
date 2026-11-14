"""Refusing recommendations the optimiser would otherwise happily make.

The search ranks rotations on simulated outcomes. It has no idea whether a crop can
physically survive at the site - it will cheerfully propose a frost-sensitive crop
for a Nebraska winter if the water balance happens to look good, because a water
balance does not know about frost.

Guardrails run on the observed record, which means they return FREQUENCIES rather
than verdicts. "Killing frost during grain fill in 7 of 41 years" is a usable
number; "frost risk: high" is not, and a farmer cannot weigh it against anything.

Two levels, and the distinction is deliberate:

    REFUSE  the crop cannot work here, and no amount of management changes that.
            Removed from the search entirely.
    WARN    it can work but carries a quantified risk, or rests on something we
            cannot verify. Stays in the search, travels with the recommendation.

Almost everything is a WARN. A guardrail that refuses too readily removes options a
farmer may have good local reasons to want, and we are not in a position to overrule
them - only to tell them what the record says.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from ..agro.climate import DailyWeather, Site
from ..data.schema import Crop

# Fraction of years a hazard must occur in before it stops being a warning and
# becomes a refusal. Set high on purpose: at 1 in 3 years a farmer may still take
# the gamble knowingly, and that is their call to make.
REFUSE_FREQUENCY = 0.5

# Share of the record in which a crop must reach maturity before the crop is
# refused outright. Set low deliberately. At 0.6 the screen refused winter wheat at
# a Nebraska site where it is actually grown, because a whole-season degree-day sum
# counts the dormant winter months at zero and so understates what an overwintering
# crop achieves. Below 0.25 the crop is failing in three years out of four, which is
# a genuine impossibility rather than a hard year.
MIN_MATURITY_FREQUENCY = 0.25

# Overwintering crops spend months dormant, during which a degree-day sum
# accumulates nothing while the crop is neither growing nor dying. Whole-season GDD
# is therefore a weak test for them and the verdict says so rather than pretending
# otherwise.
OVERWINTERING_SEASON_DAYS = 220

# The crop table carries ONE cultivar per crop, so a maturity refusal is a statement
# about that cultivar rather than about the species. Screening at a cooler Nebraska
# site refused grain sorghum, which is in fact grown there - using short-season
# hybrids needing roughly 1300-1500 degree days against the 1700 in our table. The
# refusal is correct for the cultivar it was given, and saying so is more useful
# than loosening the threshold until it agrees.
CULTIVAR_CAVEAT = (
    ". This assumes the single cultivar in the crop table; a shorter-season hybrid "
    "may well succeed where this one cannot, so confirm locally before ruling the "
    "crop out"
)


class Level(str, Enum):
    PASS = "pass"
    WARN = "warn"
    REFUSE = "refuse"


class GuardrailError(ValueError):
    pass


@dataclass(frozen=True)
class Verdict:
    """One guardrail's finding, with the evidence behind it."""

    rule: str
    level: Level
    message: str
    frequency: float | None = None
    years_affected: tuple[int, ...] = ()
    needs_local_review: bool = False

    @property
    def blocks(self) -> bool:
        return self.level is Level.REFUSE

    def describe(self) -> str:
        prefix = {Level.PASS: "ok", Level.WARN: "WARN", Level.REFUSE: "REFUSE"}[self.level]
        freq = f" [{self.frequency:.0%} of years]" if self.frequency is not None else ""
        review = "  (needs local review)" if self.needs_local_review else ""
        return f"{prefix:7s} {self.rule:26s} {self.message}{freq}{review}"


@dataclass(frozen=True)
class GuardrailReport:
    """All verdicts for one crop at one site, in one planting window."""

    crop_id: str
    crop_name: str
    planting_doy: int
    verdicts: tuple[Verdict, ...] = field(default_factory=tuple)

    @property
    def refused(self) -> bool:
        return any(v.blocks for v in self.verdicts)

    @property
    def warnings(self) -> tuple[Verdict, ...]:
        return tuple(v for v in self.verdicts if v.level is Level.WARN)

    @property
    def refusals(self) -> tuple[Verdict, ...]:
        return tuple(v for v in self.verdicts if v.blocks)

    def describe(self) -> str:
        head = (
            f"{self.crop_name} sown day {self.planting_doy}: "
            f"{'REFUSED' if self.refused else 'allowed'}"
        )
        return "\n".join([head] + ["  " + v.describe() for v in self.verdicts])


# ---------------------------------------------------------------- season windows


def _season_slices(
    weather: DailyWeather, planting_doy: int, duration: int
) -> list[tuple[int, int, int]]:
    """(year, start index, end index) for each year the season fits the record."""
    out = []
    for year in np.unique(weather.year):
        idx = np.flatnonzero((weather.year == year) & (weather.doy == planting_doy))
        if not len(idx):
            continue
        start = int(idx[0])
        if start + duration > len(weather):
            continue
        out.append((int(year), start, start + duration))
    return out


def _reproductive_window(crop: Crop, start: int, duration: int) -> tuple[int, int]:
    """Mid and late stages - flowering and grain fill.

    Frost and heat matter overwhelmingly here rather than during vegetative growth,
    which is why the whole season is the wrong window to test. A frost in the
    initial stage of winter wheat is normal and harmless; the same frost at anthesis
    destroys the crop.
    """
    s = crop.stages_days
    if s.total <= 0:
        return start, start + duration
    scale = duration / s.total
    begin = start + int((s.ini + s.dev) * scale)
    return begin, start + duration


# ---------------------------------------------------------------- the rules


def thermal_maturity(
    crop: Crop, weather: DailyWeather, planting_doy: int, duration: int
) -> Verdict:
    """Does the crop accumulate the heat it needs to reach maturity?

    This is the guardrail that stops a long-season crop being proposed somewhere the
    season is simply too short or too cool, which a water balance cannot detect -
    a crop can be perfectly watered and still never ripen.
    """
    seasons = _season_slices(weather, planting_doy, duration)
    if not seasons:
        return Verdict(
            "thermal maturity", Level.WARN,
            "no complete season fits the weather record; cannot assess",
        )
    required = crop.thermal.gdd_maturity_c_days
    if required <= 0:
        return Verdict("thermal maturity", Level.PASS, "no heat requirement")

    # A cover crop or green manure is terminated on purpose before it matures, so a
    # maturity requirement is the wrong test entirely - the screen refused cereal
    # rye for "not reaching maturity" in a 70-day autumn window, which is precisely
    # how a rye cover is meant to be grown. What matters for these crops is whether
    # they accumulate enough growth to be worth sowing, so that is what is reported.
    if crop.economics.yield_typical <= 0.0:
        achieved = float(np.mean([
            np.clip(weather.tmean_c[start:end] - crop.thermal.t_base_c, 0.0, None).sum()
            for _, start, end in seasons
        ]))
        share = achieved / required
        if share < 0.25:
            return Verdict(
                "growth window", Level.WARN,
                f"a cover crop is terminated before maturity, so maturity heat is "
                f"not the test - but this window supplies only {achieved:.0f} degree "
                f"days ({share:.0%} of a full season), so expect little biomass and "
                f"correspondingly little benefit",
                needs_local_review=True,
            )
        return Verdict(
            "growth window", Level.PASS,
            f"terminated before maturity by design; accumulates {achieved:.0f} "
            f"degree days in the window ({share:.0%} of a full season)",
        )

    failed = []
    for year, start, end in seasons:
        gdd = float(
            np.clip(weather.tmean_c[start:end] - crop.thermal.t_base_c, 0.0, None).sum()
        )
        if gdd < required:
            failed.append(year)
    frequency = len(failed) / len(seasons)
    reached = 1.0 - frequency

    overwintering = duration >= OVERWINTERING_SEASON_DAYS
    caveat = (
        " (an overwintering crop spends months dormant, so a whole-season "
        "degree-day sum understates what it achieves - treat this as indicative)"
        if overwintering else ""
    )

    if reached < MIN_MATURITY_FREQUENCY:
        return Verdict(
            "thermal maturity", Level.REFUSE,
            f"reaches maturity in only {reached:.0%} of years "
            f"(needs {required:.0f} growing degree days above "
            f"{crop.thermal.t_base_c:g} C)" + caveat + CULTIVAR_CAVEAT,
            frequency=frequency, years_affected=tuple(failed),
            needs_local_review=True,
        )
    if failed:
        return Verdict(
            "thermal maturity", Level.WARN,
            f"falls short of maturity heat in {len(failed)} of {len(seasons)} years"
            + caveat,
            frequency=frequency, years_affected=tuple(failed),
            needs_local_review=overwintering,
        )
    return Verdict(
        "thermal maturity", Level.PASS,
        f"accumulates {required:.0f} degree days in every year of the record",
        frequency=0.0,
    )


def frost_risk(
    crop: Crop, weather: DailyWeather, planting_doy: int, duration: int
) -> Verdict:
    """Killing frost during flowering or grain fill.

    Tested only over the reproductive window. A frost during the initial stage of
    winter wheat is normal and harmless; the same frost at anthesis destroys the
    crop, so the whole-season test would be both too strict for overwintering crops
    and too lenient for spring ones.
    """
    seasons = _season_slices(weather, planting_doy, duration)
    if not seasons:
        return Verdict("frost at flowering", Level.WARN, "cannot assess from the record")

    # For a crop with no harvest, frost is often the intended termination rather
    # than a loss - oats sown as an autumn cover are grown precisely to be
    # winter-killed, leaving a mulch and no termination cost.
    if crop.economics.yield_typical <= 0.0:
        return Verdict(
            "frost termination", Level.PASS,
            "no harvest to lose; frost may be the intended way to terminate this "
            "cover rather than a risk to it",
        )
    threshold = crop.thermal.t_frost_damage_c

    hit = []
    for year, start, end in seasons:
        begin, finish = _reproductive_window(crop, start, end - start)
        if finish > begin and float(weather.tmin_c[begin:finish].min()) <= threshold:
            hit.append(year)
    frequency = len(hit) / len(seasons)

    if frequency >= REFUSE_FREQUENCY:
        return Verdict(
            "frost at flowering", Level.REFUSE,
            f"temperature reaches {threshold:g} C during flowering or grain fill in "
            f"{frequency:.0%} of years",
            frequency=frequency, years_affected=tuple(hit),
        )
    if hit:
        return Verdict(
            "frost at flowering", Level.WARN,
            f"damaging frost during flowering or grain fill in {len(hit)} of "
            f"{len(seasons)} years; shifting the sowing date may avoid it",
            frequency=frequency, years_affected=tuple(hit),
        )
    return Verdict(
        "frost at flowering", Level.PASS,
        "no damaging frost during the reproductive window in the record",
        frequency=0.0,
    )


def heat_risk(
    crop: Crop,
    weather: DailyWeather,
    planting_doy: int,
    duration: int,
    *,
    tolerated_days: int = 5,
) -> Verdict:
    """Days above the crop's maximum temperature during the reproductive window.

    Heat at flowering sterilises rather than merely slowing growth, which is why a
    few days matter and why this cannot be inferred from a seasonal mean.

    **Never a refusal.** `t_max_c` is the temperature at which growth ceases, not
    one at which the crop dies, so heat reduces yield rather than making a crop
    impossible - and part of that cost is already carried by the water balance
    through raised evaporative demand. Treating it as a refusal screened out winter
    wheat in Finney County, Kansas, where it is the dominant crop and has been for a
    century. Farmers there accept the heat knowingly; the tool's job is to quantify
    it, not to overrule them.
    """
    seasons = _season_slices(weather, planting_doy, duration)
    if not seasons:
        return Verdict("heat at flowering", Level.WARN, "cannot assess from the record")
    threshold = crop.thermal.t_max_c

    counts, bad = [], []
    for year, start, end in seasons:
        begin, finish = _reproductive_window(crop, start, end - start)
        days = int((weather.tmax_c[begin:finish] > threshold).sum())
        counts.append(days)
        if days > tolerated_days:
            bad.append(year)
    frequency = len(bad) / len(seasons)
    mean_days = float(np.mean(counts))

    if frequency >= REFUSE_FREQUENCY:
        return Verdict(
            "heat at flowering", Level.WARN,
            f"frequent heat stress: more than {tolerated_days} days above "
            f"{threshold:g} C during flowering in {frequency:.0%} of years "
            f"(mean {mean_days:.1f} days). This reduces yield rather than making the "
            f"crop impossible, and is a cost a farmer may accept knowingly",
            frequency=frequency, years_affected=tuple(bad),
            needs_local_review=True,
        )
    if bad:
        return Verdict(
            "heat at flowering", Level.WARN,
            f"heat stress above {threshold:g} C during flowering in {len(bad)} of "
            f"{len(seasons)} years (mean {mean_days:.1f} days)",
            frequency=frequency, years_affected=tuple(bad),
        )
    return Verdict(
        "heat at flowering", Level.PASS,
        f"mean {mean_days:.1f} days above {threshold:g} C during flowering",
        frequency=0.0,
    )


def waterlogging_risk(
    crop: Crop, weather: DailyWeather, planting_doy: int, duration: int
) -> Verdict:
    """Extreme rainfall on a crop that cannot tolerate saturated soil.

    Sesame dies after a few days of saturation; rice does not care. Seasonal
    rainfall totals cannot express that difference, so this looks at extremes.
    """
    if crop.traits.waterlogging_tolerance >= 3:
        return Verdict(
            "waterlogging", Level.PASS,
            f"tolerance {crop.traits.waterlogging_tolerance}/5; extreme rainfall is "
            "not a limiting concern",
        )
    seasons = _season_slices(weather, planting_doy, duration)
    if not seasons:
        return Verdict("waterlogging", Level.WARN, "cannot assess from the record")

    hit = []
    for year, start, end in seasons:
        # Three-day totals, which is the scale on which saturation damage happens.
        window = weather.precip_mm[start:end]
        if len(window) < 3:
            continue
        rolling = np.convolve(window, np.ones(3), mode="valid")
        if rolling.max() > 100.0:
            hit.append(year)
    frequency = len(hit) / len(seasons)
    if hit:
        return Verdict(
            "waterlogging", Level.WARN,
            f"over 100 mm in three days during the season in {len(hit)} of "
            f"{len(seasons)} years, on a crop with waterlogging tolerance "
            f"{crop.traits.waterlogging_tolerance}/5",
            frequency=frequency, years_affected=tuple(hit),
        )
    return Verdict("waterlogging", Level.PASS, "no damaging rainfall extremes in the record")


def water_budget(
    irrigation_mm_yr: float, sustainable_cap_mm_yr: float | None
) -> Verdict:
    """Irrigation demand against a sustainable water budget.

    The cap has to be supplied. The groundwater record that would set it
    automatically is not available - the USGS endpoint serving historical well
    levels has been retired and its replacement does not carry them - so inventing
    a figure here would be the worst kind of guess, since it would silently decide
    which rotations are viable.
    """
    if sustainable_cap_mm_yr is None:
        if irrigation_mm_yr <= 0.0:
            return Verdict(
                "water budget", Level.PASS, "rainfed; no irrigation required"
            )
        return Verdict(
            "water budget", Level.WARN,
            f"needs {irrigation_mm_yr:.0f} mm/yr of irrigation and no sustainable "
            "limit was supplied; where groundwater is declining this is the "
            "constraint most likely to decide viability",
            needs_local_review=True,
        )
    if irrigation_mm_yr > sustainable_cap_mm_yr:
        return Verdict(
            "water budget", Level.REFUSE,
            f"needs {irrigation_mm_yr:.0f} mm/yr against a sustainable "
            f"{sustainable_cap_mm_yr:.0f} mm/yr",
        )
    headroom = sustainable_cap_mm_yr - irrigation_mm_yr
    return Verdict(
        "water budget", Level.PASS,
        f"{irrigation_mm_yr:.0f} mm/yr within the {sustainable_cap_mm_yr:.0f} mm/yr "
        f"budget ({headroom:.0f} mm/yr spare)",
    )


def market_and_seed_pathway(crop: Crop) -> Verdict:
    """Can the farmer actually sell it, and buy seed for it?

    Never a refusal, because we have no way to know - and a tool that silently
    eliminated a crop because it lacked price data would be hiding its own
    ignorance as a recommendation.
    """
    if crop.economics.yield_typical <= 0:
        return Verdict(
            "market pathway", Level.PASS,
            "no marketable product; the benefit is entirely soil-side",
        )
    if crop.economics.price_local_required:
        return Verdict(
            "market pathway", Level.WARN,
            "no local price, buyer or seed availability is known to this model; "
            "a crop with no nearby market is not an option however well it grows",
            needs_local_review=True,
        )
    return Verdict("market pathway", Level.PASS, "local price supplied")


# ---------------------------------------------------------------- assembly


def check_crop(
    crop: Crop,
    weather: DailyWeather,
    planting_doy: int,
    duration: int | None = None,
    *,
    irrigation_mm_yr: float = 0.0,
    sustainable_cap_mm_yr: float | None = None,
) -> GuardrailReport:
    """Run every guardrail for one crop in one planting window."""
    if crop.is_fallow:
        return GuardrailReport(
            crop.id, crop.name, planting_doy,
            (Verdict("fallow", Level.PASS, "no crop to assess"),),
        )
    span = duration or crop.duration_days
    if span <= 0:
        raise GuardrailError(f"{crop.id} has no duration to assess")

    verdicts = [
        thermal_maturity(crop, weather, planting_doy, span),
        frost_risk(crop, weather, planting_doy, span),
        heat_risk(crop, weather, planting_doy, span),
        waterlogging_risk(crop, weather, planting_doy, span),
        water_budget(irrigation_mm_yr, sustainable_cap_mm_yr),
        market_and_seed_pathway(crop),
    ]
    return GuardrailReport(crop.id, crop.name, planting_doy, tuple(verdicts))


def screen_crops(
    crops, weather: DailyWeather, windows: dict[str, tuple[int, int]], **kw
) -> dict[str, GuardrailReport]:
    """Screen a set of crops, each in its own planting window."""
    out = {}
    for crop in crops:
        if crop.id not in windows:
            continue
        doy, duration = windows[crop.id]
        out[crop.id] = check_crop(crop, weather, doy, duration, **kw)
    return out
