#!/usr/bin/env python
"""Measure held-out high-density astrometric and photometric RMS.

This is the precision companion to the controlled HST benchmark.  It evaluates
only matched test references satisfying the registered crowded subset:
``V <= 20`` and at least three quality-selected neighbours within 10 pixels.
The image-only method runs are unchanged.  Affine registration and the flux
zero point are fitted using matched non-test references, exactly as in the
existing controlled measurement protocol.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
from photutils.detection import DAOStarFinder
from photutils.psf import CircularGaussianPRF, PSFPhotometry, SourceGrouper
from scipy.spatial import cKDTree


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "wpdc"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import hst_acsggct_benchmark as benchmark
import hst_epsf_deblend_artificial_stars as epsf
import hst_spatial_epsf_joint_pilot as spatial_epsf
import real_data_domain_adaptation as adapt
import real_data_zero_shot_generalization as base


DEFAULT_OUTPUT = ROOT / "results" / "hst_high_density_measurement_precision"
METHODS = ("photutils_psf", "astrocfr_spatial_epsf_joint")


def estimate_fwhm(image: np.ndarray, rms: float) -> float:
    pre = base.detect_sources(image, rms, fwhm=2.0, threshold_sigma=10.0)
    module = adapt.load_pipeline()
    return float(np.clip(module.estimate_psf_fwhm(image, pre, rms, min_snr=20, max_sources=40), 1.5, 4.0))


def photutils_psf(image: np.ndarray, rms: float, fwhm: float) -> tuple[np.ndarray, np.ndarray]:
    finder = DAOStarFinder(fwhm=fwhm, threshold=3 * rms, sharpness_range=(0.05, 2.0),
                           roundness_range=(-1.0, 1.0), exclude_border=True)
    phot = PSFPhotometry(CircularGaussianPRF(fwhm=fwhm), fit_shape=(9, 9), finder=finder,
                         grouper=SourceGrouper(min_separation=2.0), aperture_radius=3.0,
                         fitter_maxiters=30, group_warning_threshold=1000, progress_bar=False)
    table = phot(image)
    if len(table) == 0:
        return np.empty((0, 2)), np.empty(0)
    good = (np.isfinite(table["x_fit"]) & np.isfinite(table["y_fit"])
            & np.isfinite(table["flux_fit"]) & (np.asarray(table["flux_fit"], float) > 0))
    return (np.c_[np.asarray(table["x_fit"][good], float), np.asarray(table["y_fit"][good], float)],
            np.asarray(table["flux_fit"][good], float))


def spatial_epsf_joint(image: np.ndarray, rms: float) -> tuple[np.ndarray, np.ndarray, dict]:
    sources = base.detect_sources(image, rms, fwhm=2.2, threshold_sigma=3.0)
    if len(sources) == 0:
        return np.empty((0, 2)), np.empty(0), {"initial_proposals": 0, "residual_candidates_added": 0}
    initial = np.c_[np.asarray(sources["xcentroid"], float), np.asarray(sources["ycentroid"], float)]
    global_psf, _ = epsf.build_epsf(image, sources)
    detections, _, _, residual_added = epsf.residual_candidates(image, rms, global_psf, initial)
    grid = spatial_epsf.build_quadrant_psfs(image, sources)
    fitted, flux = spatial_epsf.fit_catalogue_spatial(image, grid, detections, passes=2)
    good = np.isfinite(fitted).all(axis=1) & np.isfinite(flux) & (flux > 0)
    return fitted[good], flux[good], {
        "initial_proposals": int(len(initial)),
        "residual_candidates_added": int(residual_added),
        "quadrant_psf_stamps": {f"{ix},{iy}": int(item[1]) for (ix, iy), item in grid.items()},
    }


def high_density_metrics(cluster: str, xy: np.ndarray, flux: np.ndarray) -> dict:
    """Return RMS only for matched high-density references in the test cell."""
    _, catalogue = benchmark.read_cluster(cluster)
    x, y, _, quality, _ = benchmark.catalog_subsets(catalogue)
    reference = np.c_[x[quality], y[quality]]
    magnitude = np.asarray(catalogue["Vvega"], float)[quality]
    partition = benchmark.ref_cells(reference)
    tree = cKDTree(reference)
    neighbour_count = np.asarray([len(tree.query_ball_point(point, 10.0)) - 1 for point in reference])
    dense = (magnitude <= 20) & (neighbour_count >= 3)
    detected = xy + np.array([benchmark.CROP_X0, benchmark.CROP_Y0])
    matched, ref_index = benchmark.one_to_one(detected, reference)
    candidate_index = np.flatnonzero(matched)
    ref_index = ref_index[matched]
    train = partition[ref_index] != 2
    test_dense = (partition[ref_index] == 2) & dense[ref_index]
    result = {
        "dense_reference_test_n": int(np.sum((partition == 2) & dense)),
        "matched_test_dense_n": int(test_dense.sum()),
        "matched_non_test_n": int(train.sum()),
    }
    if train.sum() < 10 or test_dense.sum() < 5:
        return {**result, "astrometric_rms_px": None, "astrometric_rms_mas": None,
                "photometric_rms_mag": None, "status": "insufficient matched train or high-density test stars"}

    affine = benchmark.fit_affine(detected[candidate_index][train], reference[ref_index][train])
    predicted = benchmark.apply_affine(detected[candidate_index][test_dense], affine)
    delta = predicted - reference[ref_index][test_dense]
    radial = np.sqrt(np.sum(delta ** 2, axis=1))
    centre = np.median(radial)
    spread = 1.4826 * np.median(np.abs(radial - centre))
    keep_astrometry = radial <= centre + max(3 * spread, 0.05)
    astrometric_rms_px = float(np.sqrt(np.mean(np.sum(delta[keep_astrometry] ** 2, axis=1)) / 2.0))

    instrumental = -2.5 * np.log10(np.maximum(np.asarray(flux, float), 1e-6))
    zero_point = np.median(magnitude[ref_index][train] - instrumental[candidate_index][train])
    mag_residual = instrumental[candidate_index][test_dense] + zero_point - magnitude[ref_index][test_dense]
    centre = np.median(mag_residual)
    spread = 1.4826 * np.median(np.abs(mag_residual - centre))
    keep_photometry = np.abs(mag_residual - centre) <= max(3 * spread, 0.03)
    photometric_rms = float(np.sqrt(np.mean((mag_residual[keep_photometry] - np.mean(mag_residual[keep_photometry])) ** 2)))
    return {
        **result,
        "astrometric_rms_px": astrometric_rms_px,
        "astrometric_rms_mas": float(astrometric_rms_px * benchmark.PIXEL_SCALE_MAS),
        "photometric_rms_mag": photometric_rms,
        "astrometric_retained_n": int(keep_astrometry.sum()),
        "photometric_retained_n": int(keep_photometry.sum()),
        "photometric_zero_point": float(zero_point),
        "status": "ok",
    }


def run_cluster(cluster: str) -> list[dict]:
    image, _ = benchmark.read_cluster(cluster)
    subtracted, rms = base.estimate_background(image)
    fwhm = estimate_fwhm(subtracted, rms)
    rows = []
    for method in METHODS:
        started = time.perf_counter()
        if method == "photutils_psf":
            xy, flux = photutils_psf(subtracted, rms, fwhm)
            provenance = {"image_only_fwhm_px": fwhm}
        else:
            xy, flux, provenance = spatial_epsf_joint(subtracted, rms)
            provenance["image_only_fwhm_px"] = fwhm
        metrics = high_density_metrics(cluster, xy, flux)
        rows.append({"cluster": cluster, "method": method, "candidates": int(len(xy)),
                     "runtime_s": float(time.perf_counter() - started), **provenance, **metrics})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clusters", nargs="+", default=["ngc6397", "ngc6752", "ngc1851"],
                        choices=("ngc6397", "ngc6752", "ngc1851"))
    parser.add_argument("--data-dir", type=Path, required=True,
                        help="Directory containing ACSGGCT F606W images and official catalogues.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    benchmark.DATA = args.data_dir.expanduser().resolve()
    required = benchmark.DATA / "hlsp_acsggct_hst_acs-wfc_ngc6752_f606w_v2_img.fits"
    if not required.exists():
        raise SystemExit(f"ACSGGCT data are unavailable at {benchmark.DATA}.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for cluster in args.clusters:
        print(f"Running high-density precision audit on {cluster}...", flush=True)
        rows.extend(run_cluster(cluster))
        field_rows = [row for row in rows if row["cluster"] == cluster]
        (args.output_dir / f"{cluster}_high_density_precision.json").write_text(json.dumps(field_rows, indent=2), encoding="utf-8")
    payload = {
        "protocol": {
            "crowded_subset": "V <= 20 and at least three quality-selected official references within 10 pixels",
            "measurement_subset": "matched crowded references in held-out spatial test partition only",
            "astrometric_registration": "affine transform fitted from matched non-test references",
            "photometric_calibration": "one method-specific zero point fitted from matched non-test references",
            "reference_catalogue_use": "evaluation and non-test calibration only; never used for detection, ePSF construction, or fitting",
        },
        "results": rows,
    }
    (args.output_dir / "high_density_measurement_precision_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with (args.output_dir / "high_density_measurement_precision.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = sorted({key for row in rows for key in row})
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
