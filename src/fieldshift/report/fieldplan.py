"""A printable one-page field plan.

This is the artefact that survives everything else. A dashboard needs a laptop, a
signal and someone to drive it; a sheet of paper goes in the cab and comes out at
the co-op. If a farmer keeps one thing from FieldShift it will be this, so it has to
carry the rotation calendar, the handful of numbers that changed, the risks the
record actually shows, and what still needs a local answer - on one page, legibly,
in black and white.

Deliberately plain HTML with print CSS and no JavaScript, so it prints identically
from any browser and survives being emailed, saved or photographed.
"""

from __future__ import annotations

import html
from datetime import date

MONTHS = ("J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D")
MONTH_NAMES = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

# Short labels for the calendar cells. Truncating the crop name gave "Winte" for
# winter wheat, which is worse than useless on a sheet someone reads in a cab.
SHORT_NAMES = {
    "wheat_winter": "Wheat", "wheat": "Wheat", "maize": "Corn",
    "sorghum": "Sorgh", "soybean": "Soy", "pearl_millet": "Millet",
    "cereal_rye_cover": "Rye", "cotton": "Cotton", "sunn_hemp": "Hemp",
    "hairy_vetch": "Vetch", "oats_cover": "Oats",
    "sorghum_sudan_cover": "SudGr", "crimson_clover": "Clover",
    "forage_radish": "Radish", "field_pea": "Pea", "alfalfa": "Alfalfa",
    "barley": "Barley", "sunflower": "Sunfl",
}

STYLE = """
@page { size: A4; margin: 14mm; }
* { box-sizing: border-box; }
body { font: 11pt/1.45 Georgia, 'Times New Roman', serif; color: #111;
       background: #fff; margin: 0; padding: 0; }
.sheet { max-width: 190mm; margin: 0 auto; padding: 8mm 0; }
h1 { font-size: 19pt; margin: 0 0 2pt; letter-spacing: -0.2pt; }
.tagline { font-style: italic; color: #555; margin: 0 0 10pt; font-size: 10pt; }
h2 { font-size: 11pt; text-transform: uppercase; letter-spacing: 0.7pt;
     border-bottom: 1.5pt solid #111; padding-bottom: 2pt;
     margin: 14pt 0 7pt; }
.meta { font-size: 9.5pt; color: #333; margin-bottom: 4pt; }
table { width: 100%; border-collapse: collapse; font-size: 10pt; }
th, td { text-align: left; padding: 3pt 5pt; vertical-align: top; }
.numbers th { border-bottom: 1pt solid #999; font-size: 9pt;
              text-transform: uppercase; letter-spacing: 0.4pt; }
.numbers td { border-bottom: 0.5pt solid #ddd; }
.numbers td.now { color: #666; }
.numbers td.then { font-weight: bold; }
.cal { border: 1pt solid #111; table-layout: fixed; }
.cal th { text-align: center; font-size: 8pt; border-bottom: 1pt solid #111;
          background: #f2f2f2; padding: 2pt 0; }
.cal td { text-align: center; font-size: 7.5pt; height: 20pt;
          border: 0.5pt solid #ccc; padding: 1pt; }
.cal td.crop { background: #d8d8d8; font-weight: bold; }
.cal td.cover { background: #efefef; font-style: italic; }
.cal td.bare { background: #fff; color: #b00; }
.cal .yr { text-align: left; font-weight: bold; width: 16%;
           border-right: 1pt solid #111; background: #fafafa; }
ul { margin: 4pt 0 0; padding-left: 16pt; }
li { margin-bottom: 3.5pt; }
.risk li::marker { content: "! "; font-weight: bold; }
.review li::marker { content: "? "; font-weight: bold; }
.conf { border: 1pt solid #111; padding: 6pt 8pt; margin-top: 12pt;
        font-size: 9.5pt; background: #fafafa; }
.conf strong { text-transform: uppercase; letter-spacing: 0.5pt; }
.foot { margin-top: 10pt; padding-top: 6pt; border-top: 0.5pt solid #999;
        font-size: 8.5pt; color: #444; }
.bare-note { color: #b00; font-weight: bold; }
@media print { .noprint { display: none; } body { font-size: 10pt; } }
.noprint { margin-bottom: 10pt; font-family: system-ui, sans-serif;
           font-size: 9pt; color: #555; }
"""


def _e(value: object) -> str:
    return html.escape(str(value))


def _calendar(row: dict, registry) -> str:
    """Month-by-month occupancy for one cycle of the rotation.

    The most useful single picture for a farmer: it shows at a glance when the field
    is working and when it is bare, which is the thing that drives carbon loss and
    erosion exposure and the thing they can act on directly.
    """
    cycle = int(row["cycle_years"])
    grid = [["bare"] * 12 for _ in range(cycle)]
    labels = [[""] * 12 for _ in range(cycle)]

    # The bundle stores crops in calendar order with the cycle length, which is
    # enough to lay out a readable calendar without re-simulating.
    per_year: list[list[str]] = [[] for _ in range(cycle)]
    for i, crop_id in enumerate(row["crops"]):
        per_year[min(i, cycle - 1)].append(crop_id)

    WINDOWS = {
        "wheat_winter": (10, 7), "maize": (5, 10), "sorghum": (6, 10),
        "soybean": (5, 10), "pearl_millet": (6, 9), "cereal_rye_cover": (10, 12),
        "cotton": (6, 12), "sunn_hemp": (7, 8), "hairy_vetch": (10, 5),
        "oats_cover": (9, 11), "sorghum_sudan_cover": (7, 9),
    }
    for year, crops in enumerate(per_year):
        for crop_id in crops:
            crop = registry.crops.get(crop_id)
            if crop is None:
                continue
            start, end = WINDOWS.get(crop_id, (5, 9))
            is_cover = crop.economics.yield_typical <= 0
            months = (
                list(range(start, 13)) + list(range(1, end + 1))
                if end < start else list(range(start, end + 1))
            )
            for m in months:
                target_year = year
                if end < start and m < start:
                    target_year = year + 1
                if 0 <= target_year < cycle:
                    grid[target_year][m - 1] = "cover" if is_cover else "crop"
                    labels[target_year][m - 1] = SHORT_NAMES.get(
                        crop_id, crop.name.split()[0][:6]
                    )

    head = "".join(f"<th>{m}</th>" for m in MONTHS)
    rows = []
    for year in range(cycle):
        cells = []
        for m in range(12):
            kind = grid[year][m]
            text = labels[year][m] if kind != "bare" else "&mdash;"
            cells.append(f'<td class="{kind}">{text}</td>')
        rows.append(f'<tr><td class="yr">Year {year + 1}</td>{"".join(cells)}</tr>')
    return (
        '<table class="cal"><tr><th class="yr">&nbsp;</th>' + head + "</tr>"
        + "".join(rows) + "</table>"
    )


def render_field_plan(bundle: dict, row: dict, registry) -> str:
    """Build the printable plan for one rotation at one site."""
    site = bundle["site"]
    existing = next((r for r in bundle["rotations"] if r["is_existing"]), None)
    objectives = {o["key"]: o for o in bundle["objectives"]}

    number_rows = []
    for key, objective in objectives.items():
        value = row["objectives"].get(key)
        if value is None:
            continue
        now = existing["objectives"].get(key) if existing else None
        better = ""
        if now is not None and now != value:
            improved = value > now if objective["maximise"] else value < now
            better = "better" if improved else "worse"
        number_rows.append(
            "<tr>"
            f"<td>{_e(objective['label'])}</td>"
            f"<td class='now'>{'' if now is None else f'{now:.3g}'}</td>"
            f"<td class='then'>{value:.3g}</td>"
            f"<td>{_e(objective['units'])}</td>"
            f"<td>{better}</td>"
            "</tr>"
        )
    number_rows.append(
        "<tr><td>crops harvested per decade</td>"
        f"<td class='now'>{existing['detail']['crops_per_decade']:.1f}</td>"
        f"<td class='then'>{row['detail']['crops_per_decade']:.1f}</td>"
        "<td>count</td><td></td></tr>"
        if existing else ""
    )

    risks, reviews = [], []
    for entry in bundle["guardrails"].values():
        for verdict in entry["verdicts"]:
            if verdict["level"] == "refuse":
                risks.append(
                    f"<strong>{_e(entry['crop'])} is not recommended here:</strong> "
                    f"{_e(verdict['message'])}"
                )
            elif verdict["level"] == "warn":
                freq = verdict.get("frequency")
                tail = f" ({freq:.0%} of years)" if freq else ""
                risks.append(f"{_e(entry['crop'])}: {_e(verdict['message'])}{tail}")
            if verdict.get("needs_local_review"):
                reviews.append(f"{_e(entry['crop'])} &mdash; {_e(verdict['rule'])}")

    reviews += [
        "Local prices, buyers, seed availability and any contracts",
        "Your equipment, labour and storage",
        "Whether the soilborne organisms behind the rotation intervals are actually "
        "in this field",
        "Exact sowing dates, which are set locally rather than by this model",
    ]

    bare = row["objectives"].get("bare_ground")
    bare_line = (
        f"<p class='bare-note'>This rotation leaves the ground bare "
        f"{bare:.0%} of the time."
        + (
            " Where the bare season is also the dry season, wind is the dominant "
            "erosion process and this model does not quantify it."
            if bare and bare > 0.35 else ""
        )
        + "</p>"
        if bare is not None else ""
    )

    soil = site["soil"]
    weather = site["weather"]
    confidence = "MEDIUM" if site["terrain"]["slope_percent"] is not None else "LOW"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>FieldShift field plan &mdash; {_e(site['name'])}</title>
<style>{STYLE}</style></head>
<body><div class="sheet">
<div class="noprint">Printable field plan &mdash; use your browser's print command
(A4, one page).</div>

<h1>FieldShift field plan</h1>
<p class="tagline">Rethink What Grows Next.</p>

<p class="meta"><strong>{_e(site['name'])}</strong>
&nbsp;&middot;&nbsp; {site['latitude']:.3f}, {site['longitude']:.3f}
&nbsp;&middot;&nbsp; about {site.get('field_hectares', 65):.0f} ha
&nbsp;&middot;&nbsp; prepared {date.today().isoformat()}</p>
<p class="meta">{_e(soil['component'])} {_e(soil['texture'].lower())},
{soil['available_water_mm_per_m']:.0f} mm of plant-available water per metre
&nbsp;&middot;&nbsp; {site['rainfall_mm_yr']:.0f} mm rainfall a year
&nbsp;&middot;&nbsp; {weather['years']} years of NASA POWER weather
({weather['first_year']}&ndash;{weather['last_year']})</p>

<h2>Suggested rotation &mdash; {_e(row['name'])}</h2>
{_calendar(row, registry)}
<p class="meta" style="margin-top:4pt">Shaded = crop in the ground.
Italic = cover crop. <span style="color:#b00">&mdash;</span> = bare.
A {row['cycle_years']}-year cycle, then repeat.</p>
{bare_line}

<h2>What changes {'against your current rotation' if existing else ''}</h2>
<table class="numbers">
<tr><th>Measure</th><th>Now</th><th>With this plan</th><th>Units</th><th></th></tr>
{''.join(number_rows)}
</table>

<h2>Risks, counted from {weather['years']} years of weather</h2>
<ul class="risk">{''.join(f'<li>{r}</li>' for r in risks[:7]) or '<li>None flagged.</li>'}</ul>

<h2>Questions only you or your agronomist can answer</h2>
<ul class="review">{''.join(f'<li>{r}</li>' for r in dict.fromkeys(reviews).keys())}</ul>

<div class="conf">
<strong>Confidence: {confidence}.</strong>
The weather driving this plan comes from a NASA POWER cell covering roughly
270,000 hectares &mdash; about 4,000 times this field &mdash; so treat the absolute
numbers as regional and the <em>comparison between rotations</em> as the useful part.
Soil comes from the USDA survey for this map unit, not a test of your field.
</div>

<p class="foot"><strong>FieldShift supports a decision; it does not make one.</strong>
Nothing on this sheet replaces local agronomic judgement. Discuss it with your
extension agronomist before changing a rotation.</p>
</div></body></html>
"""
