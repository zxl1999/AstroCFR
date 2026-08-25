#!/usr/bin/env python
"""Catalogue-conditioned method comparison on real PHAT M31 images.

No synthetic scene is used.  DAOStarFinder, SEP, Photutils PSFPhotometry,
``astrocfr_epsf``, and the AstroCFR-Photutils hybrid receive the same real
F475W image crop.  A held-out PHAT v2 catalogue is used only after detection
for catalogue recovery and conditional measurement residuals.  Because that
catalogue is finite, unmatched detections are not labelled false positives and
the output must not be called blind precision, purity, or exhaustive
completeness.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.table import Table
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

FIELDS = ("f10", "f15", "f18")
CROP_SIZE = 1200


def phat_quality(tab: Table):
    mag = np.asarray(tab["MAG1_VEGA"], float)
    err = np.asarray(tab["MAG1_ERR"], float)
    snr = np.asarray(tab["SNR1"], float)
    sharp = np.asarray(tab["SHARP1"], float)
    crowd = np.asarray(tab["CROWD1"], float)
    flag = np.asarray(tab["FLAG1"], int)
    ra = np.asarray(tab["RA_J2000"], float)
    dec = np.asarray(tab["DEC_J2000"], float)
    keep = (np.isfinite(ra) & np.isfinite(dec) & np.isfinite(mag) &
            np.isfinite(err) & (mag < 90) & (err <= 0.10) & (snr >= 10) &
            (np.abs(sharp) <= 0.30) & (crowd <= 1.0) & (flag <= 2))
    return ra[keep], dec[keep], mag[keep]


def densest_crop(x, y, shape, crop_size):
    ny, nx = shape
    if nx <= crop_size and ny <= crop_size:
        return 0, 0
    step = 80
    xb = max(1, int(np.ceil(nx / step)))
    yb = max(1, int(np.ceil(ny / step)))
    hist, _, _ = np.histogram2d(y, x, bins=(yb, xb), range=((0, ny), (0, nx)))
    ky = max(1, int(np.ceil(crop_size / (ny / yb))))
    kx = max(1, int(np.ceil(crop_size / (nx / xb))))
    score = convolve2d(hist, np.ones((ky, kx)), mode="same", boundary="fill")
    iy, ix = np.unravel_index(np.argmax(score), score.shape)
    cx = (ix + 0.5) * nx / xb
    cy = (iy + 0.5) * ny / yb
    x0 = int(np.clip(round(cx - crop_size / 2), 0, max(0, nx - crop_size)))
    y0 = int(np.clip(round(cy - crop_size / 2), 0, max(0, ny - crop_size)))
    return x0, y0


def image_candidates(field: str, explicit: Path | None, extension: int | None):
    if explicit is not None:
        return [(explicit.resolve(), extension if extension is not None else 1)]
    base = ROOT / "external" / "non_globular_fields" / f"m31_b21_{field}"
    drz = base / "phat_f475w_drz.fits"
    if drz.exists():
        return [(drz, 1)]
    # ACS/WFC FLC layout is SCI, ERR, DQ for chip 1 then SCI, ERR, DQ for
    # chip 2.  Extension 2 is an ERR plane, never a science image.
    return [(path, ext) for path in sorted((base / "flc").glob("*_flc.fits")) for ext in (1, 4)]


def choose_real_crop(field, catalogue, image_path, extension, crop_size):
    tab = Table.read(catalogue)
    ra, dec, mag = phat_quality(tab)
    best = None
    for path, ext in image_candidates(field, image_path, extension):
        if not path.exists():
            continue
        with fits.open(path, memmap=True) as hdul:
            shape = tuple(int(v) for v in hdul[ext].data.shape)
            wcs = WCS(hdul[ext].header, fobj=hdul)
        x, y = wcs.all_world2pix(ra, dec, 0)
        valid = (np.isfinite(x) & np.isfinite(y) & (x >= 0) & (x < shape[1]) &
                 (y >= 0) & (y < shape[0]))
        if not valid.any():
            continue
        x0, y0 = densest_crop(x[valid], y[valid], shape, crop_size)
        inside = valid & (x >= x0 + 12) & (x < x0 + crop_size - 12) & (y >= y0 + 12) & (y < y0 + crop_size - 12)
        candidate = (int(inside.sum()), path, ext, x0, y0, wcs, x[inside] - x0, y[inside] - y0, mag[inside])
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        raise FileNotFoundError(f"no real F475W image overlaps {catalogue}")
    count, path, ext, x0, y0, wcs, x, y, mags = best
    with fits.open(path, memmap=True) as hdul:
        raw = np.asarray(hdul[ext].data[y0:y0 + crop_size, x0:x0 + crop_size], float).copy()
    finite = np.isfinite(raw)
    if not finite.any():
        raise RuntimeError(f"selected crop is entirely non-finite: {path}[{ext}]")
    raw[~finite] = np.nanmedian(raw[finite])
    scale = float(np.mean(proj_plane_pixel_scales(wcs.celestial)) * 3.6e6)
    return raw, np.column_stack([x, y]), mags, {
        "real_image": str(path.relative_to(ROOT)).replace("\\", "/"),
        "image_extension": int(ext),
        "crop_origin_xy": [int(x0), int(y0)],
        "crop_size": int(crop_size),
        "quality_references": int(count),
        "pixel_scale_mas": scale,
        "external_catalogue": str(catalogue.relative_to(ROOT)).replace("\\", "/"),
        "catalogue_identity": catalogue.name,
        "catalogue_use": "external post-detection held-out reference only",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--field", choices=FIELDS, default="f15")
    parser.add_argument("--catalogue", type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--extension", type=int)
    parser.add_argument("--crop-size", type=int, default=CROP_SIZE)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    field_id = f"m31_b21_{args.field}"
    catalogue = (args.catalogue or ROOT / "external" / "reference_catalogs" /
                 f"phat_b21_{args.field}_v2_phot.fits.gz").resolve()
    output = args.output_dir or ROOT / "results" / "real_field_4plus10" / field_id
    output.mkdir(parents=True, exist_ok=True)
    raw, refs, mags, audit = choose_real_crop(args.field, catalogue, args.image, args.extension, args.crop_size)
    image, rms = imageops.estimate_background(raw)
    bright = imageops.detect_sources(image, rms, fwhm=2.0, threshold_sigma=10.0)
    fwhm = float(np.clip(candidate_features.estimate_psf_fwhm(image, bright, rms, min_snr=20, max_sources=40), 1.5, 4.0))
    audit.update({"background_rms": float(rms), "image_only_fwhm_px": fwhm,
                  "truth_caveat": "finite PHAT v2 catalogue; catalogue-conditioned recovery only"})
    rows = []
    detections = {}
    for method in common.METHODS:
        print(f"{field_id}: {method}", flush=True)
        try:
            (xy, flux), elapsed, memory = common.measured(lambda m=method: common.run_method(m, image, rms, fwhm))
            detections[method] = xy
            np.savez_compressed(output / f"{method}_catalogue.npz", xy=xy, flux=flux)
            rows.append(common.evaluate(field_id, method, xy, flux, refs, mags, elapsed, memory,
                                        args.crop_size, band_name="f475w",
                                        pixel_scale_mas=audit["pixel_scale_mas"]))
        except Exception as exc:
            rows.append({"field": field_id, "method": method, "label": common.LABELS[method],
                         "error": f"{type(exc).__name__}: {exc}"})
    paired = []
    if "astrocfr_epsf" in detections and "photutils_psf" in detections:
        test_refs = refs[common.spatial_partition(refs) == 2]
        a, _ = common.one_to_one(test_refs, detections["astrocfr_epsf"])
        p, _ = common.one_to_one(test_refs, detections["photutils_psf"])
        a_only = int(np.sum(a & ~p)); p_only = int(np.sum(p & ~a)); discordant = a_only + p_only
        rng = np.random.default_rng(20260813 + len(test_refs))
        values = a.astype(float) - p.astype(float)
        boot = [float(np.mean(rng.choice(values, len(values), replace=True))) for _ in range(10000)]
        paired.append({"field": field_id, "method_a": "astrocfr_epsf", "method_b": "photutils_psf",
                       "test_references": int(len(test_refs)), "a_only": a_only, "b_only": p_only,
                       "paired_recovery_difference": float(np.mean(values)),
                       "paired_bootstrap_ci95": [float(v) for v in np.percentile(boot, [2.5, 97.5])],
                       "mcnemar_exact_p": float(binomtest(min(a_only, p_only), discordant, 0.5).pvalue) if discordant else 1.0})
    payload = {
        "protocol": {
            "scope": "single real PHAT F475W image crop; no simulated scene",
            "methods": list(common.METHODS),
            "association_radius_px": common.MATCH_RADIUS,
            "spatial_protocol": "200-pixel vertical stripes modulo 3; partition 2 held out",
            "reporting_boundary": "finite external catalogue; no blind precision/FDR label",
        },
        "field_audit": audit,
        "results": rows,
        "paired_recovery_tests": paired,
    }
    (output / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    fields_csv = sorted({key for row in rows for key in row})
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields_csv); writer.writeheader(); writer.writerows(rows)
    print(output / "summary.json")


if __name__ == "__main__":
    main()
