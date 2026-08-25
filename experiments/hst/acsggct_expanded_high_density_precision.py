#!/usr/bin/env python
"""High-density RMS comparison on four additional ACSGGCT clusters.

The additional fields use the same central 1200-pixel F606W crop, Anderson
catalogue quality cuts, image-only source fitting, vertical spatial hold-out,
and V<=20 / >=3-neighbour dense subset as the registered three-cluster audit.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.table import Table
from photutils.detection import DAOStarFinder
from photutils.psf import CircularGaussianPRF, PSFPhotometry, SourceGrouper
from scipy.spatial import cKDTree


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src" / "wpdc"
for path in (SRC, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import angst_non_globular_baseline as common
import candidate_features
import hst_epsf_deblend_artificial_stars as epsf
import hst_spatial_epsf_joint_pilot as spatial_epsf
import hst_unified_baseline_benchmark as baseline
import real_data_zero_shot_generalization as imageops


DATA = ROOT / "external" / "acsggct_expanded"
OUTPUT = ROOT / "results" / "acsggct_expanded_high_density_precision"
CLUSTERS = ("ngc2808", "ngc5286", "ngc6388", "ngc6441", "ngc0104", "ngc0362", "ngc6093", "ngc6624", "ngc6397", "ngc6752", "ngc1851")
PIXEL_SCALE_MAS = 50.0
CROP_SIZE = 1200


def load_cluster(cluster: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    prefix = f"hlsp_acsggct_hst_acs-wfc_{cluster}"
    image_path = DATA / f"{prefix}_f606w_v2_img.fits"
    catalogue_path = DATA / f"{prefix}_r.rdviq.cal.adj.zpt"
    if image_path.stat().st_size != 144002880:
        raise RuntimeError(f"{image_path.name} is incomplete: {image_path.stat().st_size} bytes")
    image = fits.getdata(image_path).astype(float)
    catalogue = Table.read(catalogue_path, format="ascii")
    x0 = (image.shape[1] - CROP_SIZE) // 2
    y0 = (image.shape[0] - CROP_SIZE) // 2
    crop = image[y0:y0 + CROP_SIZE, x0:x0 + CROP_SIZE].copy()
    bad = (~np.isfinite(crop)) | (crop <= -700)
    crop[bad] = np.nanmedian(crop[~bad])
    x = np.asarray(catalogue["x"], float) - 1.0 - x0
    y = np.asarray(catalogue["y"], float) - 1.0 - y0
    mag = np.asarray(catalogue["Vvega"], float)
    err = np.asarray(catalogue["err"], float)
    qfit = np.asarray(catalogue["qfitV"], float)
    oth = np.asarray(catalogue["othv"], float)
    nv = np.asarray(catalogue["Nv"], int)
    inside = (x >= 12) & (x < CROP_SIZE - 12) & (y >= 12) & (y < CROP_SIZE - 12)
    quality = (inside & np.isfinite(mag) & (mag < 90) & (err < 0.10) &
               (qfit < 0.30) & (oth < 1.0) & (nv >= 1))
    return crop, np.c_[x[quality], y[quality]], mag[quality], {
        "image": str(image_path), "catalogue": str(catalogue_path), "crop_origin_xy": [x0, y0],
        "quality_references": int(quality.sum()), "catalogue_total": int(len(catalogue)),
    }


def estimate_fwhm(image: np.ndarray, rms: float) -> float:
    bright = imageops.detect_sources(image, rms, fwhm=2.0, threshold_sigma=10.0)
    return float(np.clip(candidate_features.estimate_psf_fwhm(image, bright, rms, min_snr=20, max_sources=40), 1.5, 4.0))


def photutils_psf(image: np.ndarray, rms: float, fwhm: float) -> tuple[np.ndarray, np.ndarray]:
    finder = DAOStarFinder(fwhm=fwhm, threshold=3 * rms, sharpness_range=(0.05, 2.0),
                           roundness_range=(-1.0, 1.0), exclude_border=True)
    phot = PSFPhotometry(CircularGaussianPRF(fwhm=fwhm), fit_shape=(9, 9), finder=finder,
                         grouper=SourceGrouper(min_separation=2.0), aperture_radius=3.0,
                         fitter_maxiters=30, group_warning_threshold=1000, progress_bar=False)
    table = phot(image)
    good = (np.isfinite(table["x_fit"]) & np.isfinite(table["y_fit"])
            & np.isfinite(table["flux_fit"]) & (np.asarray(table["flux_fit"], float) > 0))
    return (np.c_[np.asarray(table["x_fit"][good], float), np.asarray(table["y_fit"][good], float)],
            np.asarray(table["flux_fit"][good], float))


def spatial_epsf_joint(image: np.ndarray, rms: float) -> tuple[np.ndarray, np.ndarray, dict]:
    sources = imageops.detect_sources(image, rms, fwhm=2.2, threshold_sigma=3.0)
    if len(sources) == 0:
        return np.empty((0, 2)), np.empty(0), {"initial_proposals": 0, "residual_candidates_added": 0}
    initial = np.c_[np.asarray(sources["xcentroid"], float), np.asarray(sources["ycentroid"], float)]
    global_psf, _ = epsf.build_epsf(image, sources)
    candidates, _, _, residual_added = epsf.residual_candidates(image, rms, global_psf, initial)
    grid = spatial_epsf.build_quadrant_psfs(image, sources)
    fitted, flux = spatial_epsf.fit_catalogue_spatial(image, grid, candidates, passes=2)
    good = np.isfinite(fitted).all(axis=1) & np.isfinite(flux) & (flux > 0)
    return fitted[good], flux[good], {
        "initial_proposals": int(len(initial)), "residual_candidates_added": int(residual_added),
        "quadrant_psf_stamps": {f"{ix},{iy}": int(item[1]) for (ix, iy), item in grid.items()},
    }


def high_density_metrics(xy: np.ndarray, flux: np.ndarray, refs: np.ndarray, mags: np.ndarray) -> dict:
    partition = common.spatial_partition(refs)
    tree = cKDTree(refs)
    neighbours = np.asarray([len(tree.query_ball_point(point, 10.0)) - 1 for point in refs])
    dense = (mags <= 20) & (neighbours >= 3)
    matched, reference_index = common.one_to_one(xy, refs)
    candidate_index = np.flatnonzero(matched)
    reference_index = reference_index[matched]
    train = partition[reference_index] != 2
    test_dense = (partition[reference_index] == 2) & dense[reference_index]
    result = {"dense_reference_test_n": int(np.sum((partition == 2) & dense)),
              "matched_test_dense_n": int(test_dense.sum()), "matched_non_test_n": int(train.sum())}
    if train.sum() < 10 or test_dense.sum() < 5:
        return {**result, "status": "insufficient matched train or high-density test stars",
                "astrometric_rms_mas": None, "photometric_rms_mag": None}
    affine = baseline.old.fit_affine(xy[candidate_index][train], refs[reference_index][train])
    delta = baseline.old.apply_affine(xy[candidate_index][test_dense], affine) - refs[reference_index][test_dense]
    radial = np.sqrt(np.sum(delta ** 2, axis=1))
    median = np.median(radial)
    mad = 1.4826 * np.median(np.abs(radial - median))
    keep_astrometry = radial <= median + max(3 * mad, 0.05)
    astrometric_rms_px = np.sqrt(np.mean(np.sum(delta[keep_astrometry] ** 2, axis=1)) / 2.0)
    instrumental = -2.5 * np.log10(np.maximum(np.asarray(flux, float), 1e-6))
    zero_point = np.median(mags[reference_index][train] - instrumental[candidate_index][train])
    residual = instrumental[candidate_index][test_dense] + zero_point - mags[reference_index][test_dense]
    median = np.median(residual)
    mad = 1.4826 * np.median(np.abs(residual - median))
    keep_photometry = np.abs(residual - median) <= max(3 * mad, 0.03)
    photometric_rms = np.sqrt(np.mean((residual[keep_photometry] - np.mean(residual[keep_photometry])) ** 2))
    return {**result, "status": "ok", "astrometric_rms_px": float(astrometric_rms_px),
            "astrometric_rms_mas": float(astrometric_rms_px * PIXEL_SCALE_MAS),
            "photometric_rms_mag": float(photometric_rms),
            "astrometric_retained_n": int(keep_astrometry.sum()),
            "photometric_retained_n": int(keep_photometry.sum()), "photometric_zero_point": float(zero_point)}


def run_cluster(cluster: str) -> tuple[list[dict], dict]:
    raw, refs, mags, audit = load_cluster(cluster)
    image, rms = imageops.estimate_background(raw)
    fwhm = estimate_fwhm(image, rms)
    audit.update({"background_rms": float(rms), "image_only_fwhm_px": fwhm})
    rows = []
    for method in ("photutils_psf", "astrocfr_spatial_epsf_joint"):
        started = time.perf_counter()
        if method == "photutils_psf":
            xy, flux = photutils_psf(image, rms, fwhm)
            provenance = {}
        else:
            xy, flux, provenance = spatial_epsf_joint(image, rms)
        rows.append({"cluster": cluster, "method": method, "candidates": int(len(xy)),
                     "runtime_s": float(time.perf_counter() - started), **provenance,
                     **high_density_metrics(xy, flux, refs, mags)})
    return rows, audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clusters", nargs="+", choices=CLUSTERS, default=list(CLUSTERS))
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows, audits = [], {}
    for cluster in args.clusters:
        print(f"Running expanded high-density precision audit on {cluster}...", flush=True)
        field_rows, audit = run_cluster(cluster)
        rows.extend(field_rows); audits[cluster] = audit
        (args.output_dir / f"{cluster}_high_density_precision.json").write_text(json.dumps(field_rows, indent=2), encoding="utf-8")
    payload = {
        "protocol": {
            "crowded_subset": "V <= 20 and at least three quality-selected Anderson catalogue references within 10 pixels",
            "measurement_subset": "matched crowded references in held-out vertical spatial stripes only",
            "calibration": "method-specific affine registration and photometric zero point fit from matched non-test stripes",
            "reference_catalogue_use": "evaluation and non-test calibration only; never used by candidate generation, ePSF construction, or fitting",
        },
        "audits": audits, "results": rows,
    }
    (args.output_dir / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with (args.output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = sorted({key for row in rows for key in row})
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
