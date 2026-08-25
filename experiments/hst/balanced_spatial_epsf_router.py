#!/usr/bin/env python
"""Image-only density router for the AstroCFR/Photutils balanced branch.

AstroCFR's ePSF and residual-deblend stage provides one common candidate list.
Candidates in locally crowded regions are measured by the spatial-ePSF,
neighbour-aware joint fitter.  Isolated candidates are measured by Photutils
PSFPhotometry with its conventional Gaussian PRF.  The route is determined
only from the image-derived candidate density; reference catalogues enter only
the final, held-out evaluation.

This script is deliberately an experimental result generator.  It is not wired
into the manuscript until the three-field controlled comparison is reviewed.
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

# The established HST benchmark modules live in ``experiments/hst`` while the
# reusable AstroCFR modules live in ``src/wpdc``.  Make direct script execution
# reproduce the documented ``PYTHONPATH=src/wpdc`` setup.
ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "wpdc"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import hst_acsggct_benchmark as benchmark
import hst_epsf_deblend_artificial_stars as epsf
import hst_spatial_epsf_joint_pilot as spatial_epsf
import real_data_domain_adaptation as adapt
import real_data_zero_shot_generalization as base


DEFAULT_OUTPUT = ROOT / "results" / "hst_balanced_spatial_epsf_router"


def wilson(k: int, n: int, z: float = 1.959963984540054) -> list[float | None]:
    if n == 0:
        return [None, None]
    p = k / n
    den = 1 + z * z / n
    ctr = (p + z * z / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [float(max(0, ctr - half)), float(min(1, ctr + half))]


def estimate_fwhm(image: np.ndarray, rms: float) -> float:
    """Use the same image-only FWHM estimate as the controlled baseline."""
    pre = base.detect_sources(image, rms, fwhm=2.0, threshold_sigma=10.0)
    module = adapt.load_pipeline()
    return float(np.clip(module.estimate_psf_fwhm(image, pre, rms, min_snr=20, max_sources=40), 1.5, 4.0))


def native_photutils_catalogue(image: np.ndarray, rms: float, fwhm: float) -> tuple[np.ndarray, np.ndarray]:
    """Run the unmodified Photutils detection-and-measurement branch.

    Low-density sources must come from Photutils' own conservative finder.
    Refitting every AstroCFR proposal with Photutils is the negative ablation
    already documented in this project and broadens the low-density catalogue.
    """
    finder = DAOStarFinder(
        fwhm=fwhm, threshold=3 * rms, sharpness_range=(0.05, 2.0),
        roundness_range=(-1.0, 1.0), exclude_border=True,
    )
    phot = PSFPhotometry(
        CircularGaussianPRF(fwhm=fwhm), fit_shape=(9, 9), finder=finder,
        grouper=SourceGrouper(min_separation=2.0), aperture_radius=3.0,
        fitter_maxiters=30, group_warning_threshold=1000, progress_bar=False,
    )
    table = phot(image)
    if len(table) == 0:
        return np.empty((0, 2)), np.empty(0)
    good = (np.isfinite(table["x_fit"]) & np.isfinite(table["y_fit"])
            & np.isfinite(table["flux_fit"]) & (np.asarray(table["flux_fit"], float) > 0))
    return (np.c_[np.asarray(table["x_fit"][good], float), np.asarray(table["y_fit"][good], float)],
            np.asarray(table["flux_fit"][good], float))


def route_measurements(
    image: np.ndarray,
    rms: float,
    candidates: np.ndarray,
    grid: dict,
    fwhm: float,
    density_radius_px: float,
    high_density_candidate_count: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Merge native Photutils low-density and spatial-ePSF crowded catalogues.

    This preserves the native Photutils operating point for isolated sources.
    The spatial model is nevertheless fitted to all AstroCFR candidates so
    every crowded group is jointly modelled before its high-density entries are
    selected.  Both detection lists and the routing feature are image-only.
    """
    tree = cKDTree(candidates)
    local_counts = np.asarray([len(tree.query_ball_point(point, density_radius_px)) - 1 for point in candidates], dtype=int)
    high_density = local_counts >= high_density_candidate_count

    spatial_xy, spatial_flux = spatial_epsf.fit_catalogue_spatial(image, grid, candidates, passes=2)
    spatial_good = np.isfinite(spatial_xy).all(axis=1) & np.isfinite(spatial_flux) & (spatial_flux > 0)
    phot_xy, phot_flux = native_photutils_catalogue(image, rms, fwhm)
    phot_good = np.isfinite(phot_xy).all(axis=1) & np.isfinite(phot_flux) & (phot_flux > 0)
    phot_xy, phot_flux = phot_xy[phot_good], phot_flux[phot_good]

    # Assign native Photutils entries by the closest AstroCFR proposal.  When
    # there is no close proposal, count image-only proposals around the native
    # Photutils location directly; this retains conservative native detections.
    nearest_distance, nearest = tree.query(phot_xy, k=1)
    direct_counts = np.asarray([len(tree.query_ball_point(point, density_radius_px)) for point in phot_xy], dtype=int)
    phot_counts = np.where(nearest_distance <= 2.0, local_counts[nearest], direct_counts)
    phot_high = phot_counts >= high_density_candidate_count

    selected_spatial = high_density & spatial_good
    selected_photutils = ~phot_high
    pieces_xy = [spatial_xy[selected_spatial], phot_xy[selected_photutils]]
    pieces_flux = [spatial_flux[selected_spatial], phot_flux[selected_photutils]]
    pieces_dense = [np.ones(selected_spatial.sum(), dtype=bool), np.zeros(selected_photutils.sum(), dtype=bool)]

    # Retain a native Photutils value only as an explicit numerical fallback if
    # the corresponding high-density spatial fit failed.  No reference labels
    # enter this decision.
    failed_spatial_nearest = (~spatial_good[nearest]) & phot_high & (nearest_distance <= 2.0)
    pieces_xy.append(phot_xy[failed_spatial_nearest])
    pieces_flux.append(phot_flux[failed_spatial_nearest])
    pieces_dense.append(np.ones(failed_spatial_nearest.sum(), dtype=bool))

    xy = np.vstack(pieces_xy)
    flux = np.concatenate(pieces_flux)
    chosen_dense = np.concatenate(pieces_dense)
    # A final one-pixel de-duplication only resolves route-boundary duplicates;
    # it is well below the 2-pixel evaluation association radius.
    order = np.argsort(~chosen_dense, kind="stable")
    selected: list[int] = []
    buckets: dict[tuple[int, int], list[int]] = {}
    cell = 1.0
    for idx in order:
        bx, by = np.floor(xy[idx] / cell).astype(int)
        duplicate = False
        for cx in range(bx - 1, bx + 2):
            for cy in range(by - 1, by + 2):
                for previous in buckets.get((cx, cy), []):
                    if np.hypot(*(xy[idx] - xy[previous])) <= 1.0:
                        duplicate = True
                        break
                if duplicate:
                    break
            if duplicate:
                break
        if not duplicate:
            selected.append(int(idx))
            buckets.setdefault((bx, by), []).append(int(idx))
    selected = np.asarray(selected, dtype=int)
    xy, flux, chosen_dense = xy[selected], flux[selected], chosen_dense[selected]
    details = {
        "candidate_neighbour_counts": {
            "min": int(local_counts.min()) if len(local_counts) else 0,
            "median": float(np.median(local_counts)) if len(local_counts) else 0.0,
            "max": int(local_counts.max()) if len(local_counts) else 0,
        },
        "routed_spatial_epsf_joint": int(selected_spatial.sum()),
        "routed_native_photutils": int(selected_photutils.sum()),
        "fallback_to_native_photutils": int(failed_spatial_nearest.sum()),
        "low_density_spatial_only_candidates": 0,
        "final_deduplicated_candidates": int(len(xy)),
    }
    return xy, flux, chosen_dense, details


def measurement_metrics_by_route(
    detected: np.ndarray, flux: np.ndarray, route_dense: np.ndarray,
    reference: np.ndarray, magnitude: np.ndarray, partitions: np.ndarray,
) -> dict:
    """Calibrate the two image-model flux scales only on non-test matches."""
    matched, reference_index = benchmark.one_to_one(detected, reference)
    if matched.sum() < 20:
        return {"astrometric_rms_px": None, "astrometric_rms_mas": None,
                "photometric_rms_mag": None, "measurement_test_matches": int(matched.sum())}
    candidate_index = np.flatnonzero(matched)
    reference_index = reference_index[matched]
    train = partitions[reference_index] != 2
    test = partitions[reference_index] == 2
    if train.sum() < 10 or test.sum() < 5:
        return {"astrometric_rms_px": None, "astrometric_rms_mas": None,
                "photometric_rms_mag": None, "measurement_test_matches": int(test.sum())}
    affine = benchmark.fit_affine(detected[candidate_index][train], reference[reference_index][train])
    delta = benchmark.apply_affine(detected[candidate_index][test], affine) - reference[reference_index][test]
    radial = np.sqrt(np.sum(delta ** 2, axis=1))
    med = np.median(radial)
    mad = 1.4826 * np.median(np.abs(radial - med))
    astrometric_good = radial <= med + max(3 * mad, 0.05)
    rms_px = np.sqrt(np.mean(np.sum(delta[astrometric_good] ** 2, axis=1)) / 2.0)

    instrumental = -2.5 * np.log10(np.maximum(np.asarray(flux, float), 1e-6))
    route_at_match = route_dense[candidate_index]
    global_zero_point = np.median(magnitude[reference_index][train] - instrumental[candidate_index][train])
    residuals = []
    zero_points = {}
    for name, flag in (("photutils", False), ("spatial_epsf_joint", True)):
        mode_train = train & (route_at_match == flag)
        mode_test = test & (route_at_match == flag)
        if not mode_test.any():
            continue
        zero_point = (np.median(magnitude[reference_index][mode_train] - instrumental[candidate_index][mode_train])
                      if mode_train.sum() >= 10 else global_zero_point)
        zero_points[name] = {"zero_point": float(zero_point), "training_matches": int(mode_train.sum()),
                             "test_matches": int(mode_test.sum())}
        residuals.append(instrumental[candidate_index][mode_test] + zero_point - magnitude[reference_index][mode_test])
    mag_residual = np.concatenate(residuals)
    mmed = np.median(mag_residual)
    mmad = 1.4826 * np.median(np.abs(mag_residual - mmed))
    photometric_good = np.abs(mag_residual - mmed) <= max(3 * mmad, 0.03)
    photometric_rms = np.sqrt(np.mean((mag_residual[photometric_good] - np.mean(mag_residual[photometric_good])) ** 2))
    return {"astrometric_rms_px": float(rms_px),
            "astrometric_rms_mas": float(rms_px * benchmark.PIXEL_SCALE_MAS),
            "photometric_rms_mag": float(photometric_rms),
            "measurement_test_matches": int(test.sum()),
            "photometric_route_calibration": zero_points}


def evaluate(cluster: str, xy: np.ndarray, flux: np.ndarray, route_dense: np.ndarray, elapsed: float) -> dict:
    """Use the unchanged common HST held-out evaluation protocol."""
    _, catalogue = benchmark.read_cluster(cluster)
    x, y, measured, quality, _ = benchmark.catalog_subsets(catalogue)
    reference = np.c_[x[quality], y[quality]]
    magnitude = np.asarray(catalogue["Vvega"], float)[quality]
    partitions = benchmark.ref_cells(reference)
    detected = xy + np.array([benchmark.CROP_X0, benchmark.CROP_Y0])
    test_detected = adapt.cell_ids(xy, 200) == 2
    test_reference = partitions == 2
    match, _ = benchmark.one_to_one(detected[test_detected], reference[test_reference])
    all_reference = np.c_[x[measured], y[measured]]
    all_match, _ = benchmark.one_to_one(detected[test_detected], all_reference)
    result = {
        "cluster": cluster,
        "method": "astrocfr_balanced_spatial_epsf_photutils",
        "candidates": int(len(xy)),
        "test_references": int(test_reference.sum()),
        "test_recovered": int(match.sum()),
        "test_completeness": float(match.sum() / max(test_reference.sum(), 1)),
        "test_completeness_ci95": wilson(int(match.sum()), int(test_reference.sum())),
        "test_catalog_match_lower_bound": float(all_match.sum() / max(test_detected.sum(), 1)),
        "runtime_s": float(elapsed),
        "runtime_s_per_mpix": float(elapsed / (benchmark.CROP_SIZE ** 2) * 1e6),
    }
    for limit in (18, 20, 22):
        subset = test_reference & (magnitude <= limit)
        matched, _ = benchmark.one_to_one(detected[test_detected], reference[subset])
        result[f"recall_v_le_{limit}"] = float(matched.sum() / max(subset.sum(), 1))
        result[f"n_v_le_{limit}"] = int(subset.sum())
    tree = cKDTree(reference)
    density = np.asarray([len(tree.query_ball_point(point, 10.0)) - 1 for point in reference])
    dense = test_reference & (magnitude <= 20) & (density >= 3)
    matched, _ = benchmark.one_to_one(detected[test_detected], reference[dense])
    result["high_density_v20_recall"] = float(matched.sum() / max(dense.sum(), 1))
    result["high_density_v20_n"] = int(dense.sum())
    result["high_density_v20_ci95"] = wilson(int(matched.sum()), int(dense.sum()))
    result.update(measurement_metrics_by_route(detected, flux, route_dense, reference, magnitude, partitions))
    return result


def run_cluster(cluster: str, radius: float, threshold: int) -> dict:
    started = time.perf_counter()
    image, _ = benchmark.read_cluster(cluster)
    subtracted, rms = base.estimate_background(image)
    fwhm = estimate_fwhm(subtracted, rms)
    sources = base.detect_sources(subtracted, rms, fwhm=2.2, threshold_sigma=3.0)
    if len(sources) == 0:
        raise RuntimeError(f"No image-only proposals for {cluster}")
    initial = np.c_[np.asarray(sources["xcentroid"], float), np.asarray(sources["ycentroid"], float)]
    global_psf, _ = epsf.build_epsf(subtracted, sources)
    candidates, _, _, recovered_residual = epsf.residual_candidates(subtracted, rms, global_psf, initial)
    grid = spatial_epsf.build_quadrant_psfs(subtracted, sources)
    xy, flux, routed_dense, details = route_measurements(
        subtracted, rms, candidates, grid, fwhm, radius, threshold
    )
    elapsed = time.perf_counter() - started
    result = evaluate(cluster, xy, flux, routed_dense, elapsed)
    result.update({
        "image_only_fwhm_px": fwhm,
        "initial_proposals": int(len(initial)),
        "residual_candidates_added": int(recovered_residual),
        "retained_dense_route_candidates": int(routed_dense.sum()),
        "retained_low_density_route_candidates": int((~routed_dense).sum()),
        "quadrant_psf_stamps": {f"{ix},{iy}": int(item[1]) for (ix, iy), item in grid.items()},
        **details,
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clusters", nargs="+", default=["ngc6397", "ngc6752", "ngc1851"],
                        choices=("ngc6397", "ngc6752", "ngc1851"))
    parser.add_argument("--density-radius-px", type=float, default=10.0)
    parser.add_argument("--high-density-candidate-count", type=int, default=3)
    parser.add_argument("--data-dir", type=Path,
                        help="Directory containing the ACSGGCT FITS images and catalogues. Defaults to the benchmark's standard path.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.density_radius_px <= 0 or args.high_density_candidate_count < 1:
        raise SystemExit("Density radius must be positive and candidate count must be at least one.")
    if args.data_dir is not None:
        benchmark.DATA = args.data_dir.expanduser().resolve()
    required = benchmark.DATA / "hlsp_acsggct_hst_acs-wfc_ngc6752_f606w_v2_img.fits"
    if not required.exists():
        raise SystemExit(f"ACSGGCT data are unavailable at {benchmark.DATA}. Provide --data-dir with the existing data directory.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for cluster in args.clusters:
        print(f"Running balanced branch on {cluster}...", flush=True)
        results.append(run_cluster(cluster, args.density_radius_px, args.high_density_candidate_count))
    payload = {
        "protocol": {
            "candidate_stage": "AstroCFR image-only ePSF plus residual deblending",
            "routing_feature": f"image-only candidates within {args.density_radius_px:g} px",
            "routing_rule": f"count >= {args.high_density_candidate_count}: spatial-ePSF joint catalogue; otherwise: native Photutils Gaussian-PRF catalogue",
            "spatial_fit": "quadrant image-only empirical PSFs; two neighbour-aware passes",
            "input_data_dir": str(benchmark.DATA),
            "reference_catalogue_use": "evaluation only; never used for proposal generation, PSF construction, fitting, or routing",
            "photometric_calibration": "separate zero points for Photutils and spatial-ePSF route, fitted only from matched non-test partitions",
            "status": "experimental; requires held-out review before manuscript inclusion",
        },
        "results": results,
    }
    (args.output_dir / "balanced_spatial_epsf_router_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with (args.output_dir / "balanced_spatial_epsf_router_results.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = sorted({key for result in results for key in result})
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
