#!/usr/bin/env python
"""Map the registered M31 DOLPHOT artificial-star list to the PHAT DRZ frame.

The DOLPHOT list uses the first accepted FLC (jbex18u6q) as reference frame.
This audit converts each source through celestial coordinates into the three
accepted FLC exposures and the PHAT F475W DRZ image.  It retains only sources
whose mapped position is safely inside every image and the declared DRZ crop.

This establishes common *sky coordinates*, not pixel-identical injection
noise: DOLPHOT FakeStars is an FLC-domain in-memory injection whereas the
single-stack methods inject into a drizzled image.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS


ROOT = Path(__file__).resolve().parents[2]
FIELD = ROOT / "external" / "non_globular_fields" / "m31_b21_f15"
OUT = ROOT / "results" / "non_globular_runs" / "m31_b21_f15" / "matched_coordinate_scene"
FLC_NAMES = ("jbex18u6q_flc.fits", "jbex18u9q_flc.fits", "jbex18ucq_flc.fits")
DRZ = FIELD / "phat_f475w_drz.fits"
INPUT = ROOT / "results" / "non_globular_runs" / "m31_b21_f15" / "joint_3x370s_f475w" / "m31_fake_n800.csv"
MARGIN = 15.0


def wcs_and_shape(path: Path, extension: int):
    # DOLPHOT logical extension 1/2 corresponds to ACS SCI HDU 1/4.
    hdu = 1 if extension == 1 else 4
    with fits.open(path, memmap=True) as hdul:
        return WCS(hdul[hdu].header, fobj=hdul), tuple(hdul[hdu].data.shape)


def interior(xy: np.ndarray, shape: tuple[int, int], margin: float) -> np.ndarray:
    h, w = shape
    return ((xy[:, 0] >= margin) & (xy[:, 0] <= w - margin) &
            (xy[:, 1] >= margin) & (xy[:, 1] <= h - margin))


def main() -> None:
    rows = list(csv.DictReader(INPUT.open(encoding="utf-8")))
    with fits.open(DRZ, memmap=True) as hdul:
        drz_wcs = WCS(hdul[1].header, fobj=hdul)
        drz_shape = tuple(hdul[1].data.shape)
    flc = {name: {ext: wcs_and_shape(FIELD / "flc" / name, ext) for ext in (1, 2)} for name in FLC_NAMES}

    output = []
    excluded = {"flc_edge": 0, "drz_edge": 0}
    for idx, row in enumerate(rows):
        ext = int(row["extension"])
        ref_wcs, _ = flc[FLC_NAMES[0]][ext]
        xy = np.array([[float(row["x"]), float(row["y"])]])
        sky = ref_wcs.all_pix2world(xy, 1)
        all_inside = True
        flc_mapped = {}
        for name in FLC_NAMES:
            wcs, shape = flc[name][ext]
            p = wcs.all_world2pix(sky, 1)[0]
            flc_mapped[name] = [float(p[0]), float(p[1])]
            all_inside &= bool(interior(np.array([p]), shape, MARGIN)[0])
        if not all_inside:
            excluded["flc_edge"] += 1
            continue
        drz_xy = drz_wcs.all_world2pix(sky, 1)[0]
        if not bool(interior(np.array([drz_xy]), drz_shape, MARGIN)[0]):
            excluded["drz_edge"] += 1
            continue
        output.append({
            "fake_id": idx,
            "extension": ext,
            "density_band": row["density_band"],
            "input_vegamag_f475w": float(row["input_vegamag_f475w"]),
            "reference_flc_xy": [float(xy[0, 0]), float(xy[0, 1])],
            "drz_full_xy": [float(drz_xy[0]), float(drz_xy[1])],
            "per_flc_xy": flc_mapped,
        })

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "mapped_fake_stars.json").write_text(json.dumps({
        "source_list": str(INPUT), "accepted_flc": list(FLC_NAMES), "drz": str(DRZ),
        "drz_shape_yx": list(drz_shape), "edge_margin_px": MARGIN,
        "input_count": len(rows), "retained_count": len(output), "excluded": excluded,
        "scope": "Common sky-coordinate audit only; no assertion of pixel-identical FLC and DRZ injection.",
        "stars": output,
    }, indent=2), encoding="utf-8")
    fields = ["fake_id", "extension", "density_band", "input_vegamag_f475w", "reference_flc_x", "reference_flc_y", "drz_x", "drz_y"]
    with (OUT / "mapped_fake_stars.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for row in output:
            writer.writerow({"fake_id": row["fake_id"], "extension": row["extension"], "density_band": row["density_band"],
                             "input_vegamag_f475w": row["input_vegamag_f475w"], "reference_flc_x": row["reference_flc_xy"][0], "reference_flc_y": row["reference_flc_xy"][1],
                             "drz_x": row["drz_full_xy"][0], "drz_y": row["drz_full_xy"][1]})
    print(json.dumps({"input": len(rows), "retained": len(output), "excluded": excluded}, indent=2))


if __name__ == "__main__":
    main()
