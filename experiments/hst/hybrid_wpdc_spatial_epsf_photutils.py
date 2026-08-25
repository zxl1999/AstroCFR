#!/usr/bin/env python
"""Controlled WPDC spatial-ePSF / Photutils fitting hybrid.

The earlier hybrid replaced WPDC's empirical PSF with a Gaussian PRF and was
therefore deliberately a negative ablation.  This experiment retains image-only
WPDC residual-deblended proposals and a quadrant empirical PSF grid.  Photutils
only performs the numerical PSF fit through ``ImagePSF``.  The official catalogue
is never used until evaluation of the existing untouched spatial test cell.

This remains an exploratory ablation: a result may enter the manuscript only if
it retains the recovery operating point and improves a pre-specified held-out
measurement metric relative to the corresponding baselines.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np
from astropy.table import Table
from photutils.psf import ImagePSF, PSFPhotometry, SourceGrouper


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def evaluate(old, adapt, cluster, xy, flux, elapsed):
    """Apply precisely the controlled benchmark's held-out metrics."""
    _, cat = old.read_cluster(cluster)
    x, y, measured, quality, _ = old.catalog_subsets(cat)
    ref = np.column_stack([x[quality], y[quality]])
    mag = np.asarray(cat["Vvega"], float)[quality]
    part = old.ref_cells(ref)
    det = xy + np.array([old.CROP_X0, old.CROP_Y0])
    test_det = adapt.cell_ids(xy, 200) == 2
    test_ref = part == 2
    match, _ = old.one_to_one(det[test_det], ref[test_ref])
    allref = np.column_stack([x[measured], y[measured]])
    allmatch, _ = old.one_to_one(det[test_det], allref)
    result = {
        "cluster": cluster,
        "method": "wpdc_spatial_epsf_photutils",
        "candidates": int(len(xy)),
        "test_references": int(test_ref.sum()),
        "test_recovered": int(match.sum()),
        "test_completeness": float(match.sum() / max(test_ref.sum(), 1)),
        "test_catalog_match_lower_bound": float(allmatch.sum() / max(test_det.sum(), 1)),
        "runtime_s": float(elapsed),
        "runtime_s_per_mpix": float(elapsed / (old.CROP_SIZE ** 2) * 1e6),
    }
    for limit in (18, 20, 22):
        subset = test_ref & (mag <= limit)
        matched, _ = old.one_to_one(det[test_det], ref[subset])
        result[f"recall_v_le_{limit}"] = float(matched.sum() / max(subset.sum(), 1))
        result[f"n_v_le_{limit}"] = int(subset.sum())
    from scipy.spatial import cKDTree
    tree = cKDTree(ref)
    density = np.array([len(tree.query_ball_point(point, 10)) - 1 for point in ref])
    dense = test_ref & (mag <= 20) & (density >= 3)
    matched, _ = old.one_to_one(det[test_det], ref[dense])
    result["high_density_v20_recall"] = float(matched.sum() / max(dense.sum(), 1))
    result["high_density_v20_n"] = int(dense.sum())
    result.update(old.measurement_metrics(det, flux, np.ones(len(det), bool), ref, mag, part))
    return result


def spatial_psf_fit(image, candidates, grid, spatial, epsf):
    """Fit each candidate with its local WPDC ePSF using Photutils.

    Candidate groups are processed by ePSF quadrant.  The one-pixel halo means
    sources lying exactly on a grid border are fitted by both local models, then
    the fit closest to its proposal is retained.  This avoids a discontinuity at
    the quadrant edge without borrowing catalogue positions.
    """
    records = []
    for iy in range(2):
        for ix in range(2):
            xlo, xhi = ix * image.shape[1] / 2, (ix + 1) * image.shape[1] / 2
            ylo, yhi = iy * image.shape[0] / 2, (iy + 1) * image.shape[0] / 2
            halo = ((candidates[:, 0] >= xlo - 1) & (candidates[:, 0] < xhi + 1) &
                    (candidates[:, 1] >= ylo - 1) & (candidates[:, 1] < yhi + 1))
            proposal = candidates[halo]
            if not len(proposal):
                continue
            # The empirical stamp is normalized by WPDC; ImagePSF preserves its
            # sub-pixel spline representation and exposes flux/x/y to Photutils.
            model = ImagePSF(grid[(ix, iy)][0], origin=(epsf.HALF, epsf.HALF))
            init = Table()
            init["x_0"] = proposal[:, 0]
            init["y_0"] = proposal[:, 1]
            seed = []
            for x, y in proposal:
                px, py = int(round(x)), int(round(y))
                seed.append(max(float(image[py, px]), 1.0) if 0 <= px < image.shape[1] and 0 <= py < image.shape[0] else 1.0)
            init["flux_0"] = np.asarray(seed)
            phot = PSFPhotometry(model, fit_shape=(11, 11), finder=None,
                                 grouper=SourceGrouper(min_separation=2.0),
                                 aperture_radius=3.0, fitter_maxiters=30,
                                 group_warning_threshold=1000, progress_bar=False)
            fitted = phot(image, init_params=init)
            good = (np.isfinite(fitted["x_fit"]) & np.isfinite(fitted["y_fit"]) &
                    np.isfinite(fitted["flux_fit"]) & (fitted["flux_fit"] > 0))
            for px, py, x, y, flux in zip(proposal[good, 0], proposal[good, 1],
                                           np.asarray(fitted["x_fit"][good], float),
                                           np.asarray(fitted["y_fit"][good], float),
                                           np.asarray(fitted["flux_fit"][good], float)):
                records.append((px, py, x, y, flux))
    if not records:
        return np.empty((0, 2)), np.empty(0)
    values = np.asarray(records, float)
    # Select exactly one result per original proposal, preferring the smallest
    # displacement.  This is a deterministic de-duplication rule.
    chosen = {}
    for px, py, x, y, flux in values:
        key = (round(px, 6), round(py, 6))
        score = (x - px) ** 2 + (y - py) ** 2
        if key not in chosen or score < chosen[key][0]:
            chosen[key] = (score, x, y, flux)
    output = np.asarray([[x, y, flux] for _, x, y, flux in chosen.values()])
    return output[:, :2], output[:, 2]


def run_cluster(old, base, adapt, epsf, spatial, cluster):
    image, _ = old.read_cluster(cluster)
    sub, rms = base.estimate_background(image)
    sources = base.detect_sources(sub, rms, fwhm=2.2, threshold_sigma=3.0)
    initial = np.column_stack([np.asarray(sources["xcentroid"], float), np.asarray(sources["ycentroid"], float)])
    global_psf, _ = epsf.build_epsf(sub, sources)
    candidates, _, _, extra = epsf.residual_candidates(sub, rms, global_psf, initial)
    grid = spatial.build_quadrant_psfs(sub, sources)
    started = time.perf_counter()
    fit_xy, flux = spatial_psf_fit(sub, candidates, grid, spatial, epsf)
    elapsed = time.perf_counter() - started
    result = evaluate(old, adapt, cluster, fit_xy, flux, elapsed)
    result.update({
        "wpdc_initial_candidates": int(len(initial)),
        "wpdc_residual_candidates_added": int(extra),
        "photutils_retained": int(len(fit_xy)),
        "measurement": "Photutils ImagePSF fit with WPDC quadrant empirical PSFs",
        "quadrant_psf_stamps": {f"{ix},{iy}": int(item[1]) for (ix, iy), item in grid.items()},
    })
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", required=True, type=Path, help="Directory containing the established HST benchmark modules")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--cluster", default="ngc6752", choices=("ngc6397", "ngc6752", "ngc1851"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.work_dir.resolve()))
    old = load(args.work_dir / "hst_acsggct_benchmark.py", "spatial_hybrid_old")
    base = load(args.work_dir / "real_data_zero_shot_generalization.py", "spatial_hybrid_base")
    adapt = load(args.work_dir / "real_data_domain_adaptation.py", "spatial_hybrid_adapt")
    epsf = load(args.work_dir / "hst_epsf_deblend_artificial_stars.py", "spatial_hybrid_epsf")
    spatial = load(args.work_dir / "hst_spatial_epsf_joint_pilot.py", "spatial_hybrid_spatial")
    result = run_cluster(old, base, adapt, epsf, spatial, args.cluster)
    protocol = {
        "candidate_stage": "WPDC image-only ePSF plus residual deblending",
        "measurement_stage": "Photutils PSFPhotometry with per-quadrant WPDC ImagePSF",
        "association_radius_px": 2,
        "spatial_test_partition": 2,
        "status": "exploratory; not eligible for manuscript claims until comparison is reviewed",
    }
    payload = {"protocol": protocol, "result": result}
    (args.output_dir / f"{args.cluster}_spatial_epsf_photutils.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with (args.output_dir / f"{args.cluster}_spatial_epsf_photutils.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(result))
        writer.writeheader()
        writer.writerow(result)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
