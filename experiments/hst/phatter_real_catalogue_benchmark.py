#!/usr/bin/env python
"""Five-method comparison on real PHATTER M33 F475W ACS/WFC FLC images.

The published PHATTER table-6 catalogue is queried from the official VizieR
mirror and is used only after image-only detection.  Thus recovery and RMS are
catalogue-conditioned, never blind purity or exhaustive completeness.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from scipy.signal import convolve2d
from scipy.stats import binomtest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src" / "wpdc"))
sys.path.insert(0, str(HERE))

import angst_non_globular_baseline as common
import candidate_features
import real_data_zero_shot_generalization as imageops

FIELDS = {
    "m33_b01_f01": {"image": "jdb604t3q_flc.fits", "catalogue": "phatter_m33_b01_f01_table6_full.tsv", "tile": "b01-ne"},
    "m33_b03_f02": {"image": "jdb641gaq_flc.fits", "catalogue": "phatter_m33_b03_f02_table6_full.tsv", "tile": "b03-ne"},
}


def read_table6(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("RAJ2000\tDEJ2000\tF475W"))
    names = lines[start].split("\t")
    records = []
    for line in lines[start + 2:]:
        if not line or line.startswith("#"):
            continue
        values = line.split("\t")
        if len(values) != len(names) or values[0] in {"deg", "-----------"}:
            continue
        row = dict(zip(names, values))
        try:
            ra, dec = float(row["RAJ2000"]), float(row["DEJ2000"])
            mag, snr = float(row["F475W"]), float(row["F475Wsnr"])
        except ValueError:
            continue
        if (np.isfinite(ra) and np.isfinite(dec) and np.isfinite(mag) and
                mag < 90 and np.isfinite(snr) and snr >= 10 and row["F475Wgst"].strip() == "T"):
            records.append((ra, dec, mag))
    if not records:
        raise RuntimeError(f"no PHATTER F475W GST-quality references in {path}")
    data = np.asarray(records, float)
    return data[:, 0], data[:, 1], data[:, 2]


def densest_crop(x, y, shape, crop_size):
    ny, nx = shape
    step = 80
    xb, yb = max(1, int(np.ceil(nx / step))), max(1, int(np.ceil(ny / step)))
    hist, _, _ = np.histogram2d(y, x, bins=(yb, xb), range=((0, ny), (0, nx)))
    ky, kx = max(1, int(np.ceil(crop_size / (ny / yb)))), max(1, int(np.ceil(crop_size / (nx / xb))))
    iy, ix = np.unravel_index(np.argmax(convolve2d(hist, np.ones((ky, kx)), mode="same")), hist.shape)
    x0 = int(np.clip(round((ix + .5) * nx / xb - crop_size / 2), 0, max(0, nx - crop_size)))
    y0 = int(np.clip(round((iy + .5) * ny / yb - crop_size / 2), 0, max(0, ny - crop_size)))
    return x0, y0


def load_field(field: str, crop_size: int):
    spec = FIELDS[field]
    catalogue = ROOT / "external" / "reference_catalogs" / spec["catalogue"]
    image_path = ROOT / "external" / "non_globular_fields" / field / "flc" / spec["image"]
    ra, dec, mag = read_table6(catalogue)
    best = None
    with fits.open(image_path, memmap=True) as hdul:
        for ext in (1, 4):
            shape = tuple(int(v) for v in hdul[ext].data.shape)
            wcs = WCS(hdul[ext].header, fobj=hdul)
            x, y = wcs.all_world2pix(ra, dec, 0)
            valid = np.isfinite(x) & np.isfinite(y) & (x >= 0) & (x < shape[1]) & (y >= 0) & (y < shape[0])
            if not valid.any():
                continue
            x0, y0 = densest_crop(x[valid], y[valid], shape, crop_size)
            inside = valid & (x >= x0 + 12) & (x < x0 + crop_size - 12) & (y >= y0 + 12) & (y < y0 + crop_size - 12)
            item = (int(inside.sum()), ext, x0, y0, wcs, x[inside] - x0, y[inside] - y0, mag[inside], shape)
            if best is None or item[0] > best[0]:
                best = item
        if best is None:
            raise RuntimeError(f"PHATTER catalogue does not overlap {image_path}")
        count, ext, x0, y0, wcs, x, y, mags, shape = best
        raw = np.asarray(hdul[ext].data[y0:y0 + crop_size, x0:x0 + crop_size], float).copy()
    finite = np.isfinite(raw)
    raw[~finite] = np.nanmedian(raw[finite])
    audit = {
        "real_image": str(image_path.relative_to(ROOT)).replace("\\", "/"), "image_extension": int(ext),
        "crop_origin_xy": [int(x0), int(y0)], "full_shape_yx": list(shape), "quality_references": int(count),
        "pixel_scale_mas": float(np.mean(proj_plane_pixel_scales(wcs.celestial)) * 3.6e6),
        "external_catalogue": str(catalogue.relative_to(ROOT)).replace("\\", "/"),
        "catalogue_source": "PHATTER table 6, Williams et al. 2021, VizieR J/ApJS/253/53/table6",
        "phatter_subset": spec["tile"], "quality_cut": "F475Wgst=T and F475Wsnr>=10",
    }
    return raw, np.column_stack([x, y]), mags, audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--field", choices=tuple(FIELDS), required=True)
    parser.add_argument("--crop-size", type=int, default=1200)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output = args.output_dir or ROOT / "results" / "real_field_4plus10" / args.field
    output.mkdir(parents=True, exist_ok=True)
    raw, refs, mags, audit = load_field(args.field, args.crop_size)
    image, rms = imageops.estimate_background(raw)
    bright = imageops.detect_sources(image, rms, fwhm=2.0, threshold_sigma=10.0)
    fwhm = float(np.clip(candidate_features.estimate_psf_fwhm(image, bright, rms, min_snr=20, max_sources=40), 1.5, 4.0))
    audit.update({"background_rms_electrons": float(rms), "image_only_fwhm_px": fwhm,
                  "truth_caveat": "finite published PHATTER catalogue; catalogue-conditioned recovery only"})
    rows, detections = [], {}
    for method in common.METHODS:
        print(f"{args.field}: {method}", flush=True)
        try:
            (xy, flux), elapsed, memory = common.measured(lambda m=method: common.run_method(m, image, rms, fwhm))
            detections[method] = xy
            np.savez_compressed(output / f"{method}_catalogue.npz", xy=xy, flux=flux)
            rows.append(common.evaluate(args.field, method, xy, flux, refs, mags, elapsed, memory, image.size, band_name="f475w", pixel_scale_mas=audit["pixel_scale_mas"]))
        except Exception as exc:
            rows.append({"field": args.field, "method": method, "label": common.LABELS[method], "error": f"{type(exc).__name__}: {exc}"})
    paired = []
    if {"astrocfr_epsf", "photutils_psf"} <= detections.keys():
        test_refs = refs[common.spatial_partition(refs) == 2]
        a, _ = common.one_to_one(test_refs, detections["astrocfr_epsf"])
        p, _ = common.one_to_one(test_refs, detections["photutils_psf"])
        a_only, p_only = int(np.sum(a & ~p)), int(np.sum(p & ~a)); discordant = a_only + p_only
        values = a.astype(float) - p.astype(float); rng = np.random.default_rng(20260814 + len(test_refs))
        boot = [float(np.mean(rng.choice(values, len(values), replace=True))) for _ in range(10000)]
        paired.append({"field": args.field, "method_a": "astrocfr_epsf", "method_b": "photutils_psf", "test_references": int(len(test_refs)), "a_only": a_only, "b_only": p_only, "paired_recovery_difference": float(np.mean(values)), "paired_bootstrap_ci95": [float(v) for v in np.percentile(boot, [2.5, 97.5])], "mcnemar_exact_p": float(binomtest(min(a_only, p_only), discordant, .5).pvalue) if discordant else 1.0})
    payload = {"protocol": {"scope": "single real HST ACS/WFC F475W FLC crop; no simulated scene", "methods": list(common.METHODS), "association_radius_px": common.MATCH_RADIUS, "spatial_protocol": "200-pixel vertical stripes modulo 3; partition 2 held out", "reporting_boundary": "finite published catalogue; no blind precision/FDR label"}, "field_audit": audit, "results": rows, "paired_recovery_tests": paired}
    (output / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    keys = sorted({key for row in rows for key in row})
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys); writer.writeheader(); writer.writerows(rows)


if __name__ == "__main__":
    main()
