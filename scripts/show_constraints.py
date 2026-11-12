"""Print the rotation constraints the parameter tables imply.

Meant for review by an agronomist, not for the app. Run:
    ./.venv/bin/python scripts/show_constraints.py
"""

from __future__ import annotations

import sys

from rdss.data import load_registry

CANDIDATE_ROTATIONS = [
    (["rice_lowland", "wheat"], 1, "the baseline it is usually worth challenging"),
    (["rice_lowland", "wheat", "sunn_hemp", "chickpea"], 1, "green manure + pulse break"),
    (["cotton", "wheat", "cotton", "wheat"], 1, "continuous cotton under a cereal"),
    (["potato", "mustard", "potato", "mustard"], 1, "two long-interval crops stacked"),
    (["maize", "chickpea", "pearl_millet", "mungbean"], 1, "low-water diversified"),
    (["rice_lowland", "wheat", "mungbean"], 2, "double-cropped, 2 slots per year"),
]


def main() -> int:
    reg = load_registry()

    print(f"{len(reg.crops)} crops, {len(reg.pathogens)} pathogens\n")

    print("=== Minimum return interval by crop ===")
    rows = sorted(
        (reg.min_return_interval(c.id) for c in reg.crops.values()),
        key=lambda c: (-c.years, c.crop_id),
    )
    for c in rows:
        print(
            f"  {reg.crop(c.crop_id).name:38s} {c.years}y   "
            f"{c.driver:28s} [{c.efficacy}/{c.confidence}]"
        )

    print("\n=== Rotation screening ===")
    for seq, spy, label in CANDIDATE_ROTATIONS:
        names = " -> ".join(reg.crop(c).name for c in seq)
        vs = reg.check_rotation(seq, slots_per_year=spy, cyclic=True)
        verdict = "OK" if not vs else ("WORKABLE" if reg.rotation_is_feasible(
            seq, slots_per_year=spy, cyclic=True) else "REJECT")
        print(f"\n  [{verdict}] {names}")
        print(f"         ({label}; {spy} slot(s)/year, cycle closed)")
        for v in vs:
            print(f"         - {v.severity.upper()}: {v.describe()}")

    print("\n=== Soil texture classes ===")
    print(f"  {'class':20s} {'TAW':>8s} {'REW':>6s} {'Ksat':>8s}  grp  sand/silt/clay")
    for s in sorted(reg.textures.values(), key=lambda s: -s.taw_mm_per_m):
        print(
            f"  {s.name:20s} {s.taw_mm_per_m:6.0f}mm/m {s.rew_mm:5.1f} "
            f"{s.ksat_mm_h:7.1f}mm/h  {s.hydrologic_group}    "
            f"{s.sand_pct:.0f}/{s.silt_pct:.0f}/{s.clay_pct:.0f}"
        )

    print("\n=== Data gaps that need local review ===")
    gaps = (
        reg.crop_notes["known_gaps"]
        + reg.pathogen_notes["known_gaps"]
        + reg.soil_notes["known_gaps"]
    )
    for note in gaps:
        print(f"  - {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
