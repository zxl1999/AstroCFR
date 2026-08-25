#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Non-manuscript pilot: quadrant ePSF plus neighbour-aware joint fitting.

This is intentionally an ablation, not a replacement for the published
WPDC ePSF branch.  It uses no reference positions while estimating PSFs or
fitting sources.  A result is eligible for the manuscript only if it improves
the held-out measurement metrics without losing the stated recovery advantage.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares, nnls
from scipy.spatial import cKDTree

import hst_acsggct_benchmark as old
import hst_epsf_deblend_artificial_stars as epsf
import real_data_zero_shot_generalization as base


HERE = Path(__file__).resolve().parent
OUT = HERE / "hst_spatial_epsf_joint_pilot_results"


def build_quadrant_psfs(image, sources):
    """Build four image-only ePSFs, each from sources inside one quadrant."""
    x = np.asarray(sources["xcentroid"], float)
    y = np.asarray(sources["ycentroid"], float)
    result = {}
    for iy in range(2):
        for ix in range(2):
            mask = ((x >= ix * old.CROP_SIZE / 2) & (x < (ix + 1) * old.CROP_SIZE / 2)
                    & (y >= iy * old.CROP_SIZE / 2) & (y < (iy + 1) * old.CROP_SIZE / 2))
            local = sources[mask]
            # A quadrant can lack isolated stamps in the most crowded core.
            # In that case use the global image-only ePSF and disclose it.
            try:
                psf, n = epsf.build_epsf(image, local)
                result[(ix, iy)] = (psf, n, "quadrant")
            except RuntimeError:
                psf, n = epsf.build_epsf(image, sources)
                result[(ix, iy)] = (psf, n, "global-fallback")
    return result


def psf_at(grid, x, y):
    ix = int(np.clip(x * 2 // old.CROP_SIZE, 0, 1))
    iy = int(np.clip(y * 2 // old.CROP_SIZE, 0, 1))
    return grid[(ix, iy)][0]


def fit_one_spatial(image, grid, x0, y0, neighbours):
    """Refit one centroid while solving all local neighbour amplitudes jointly."""
    item = epsf.local_patch(image, x0, y0, half=5)
    if item is None:
        return x0, y0, np.nan
    patch, ix, iy = item
    yy, xx = np.mgrid[iy - 5:iy + 6, ix - 5:ix + 6]
    yy = yy.astype(float)
    xx = xx.astype(float)
    other = np.asarray(neighbours, float)
    edge = np.r_[patch[0], patch[-1], patch[:, 0], patch[:, -1]]
    background = float(np.median(edge))

    def solve(par):
        xc, yc = par
        coords = np.vstack([[xc, yc], other]) if len(other) else np.array([[xc, yc]])
        cols = [epsf.psf_values(psf_at(grid, a, b), xx, yy, a, b).ravel() for a, b in coords]
        design = np.column_stack(cols)
        coeff, _ = nnls(design, patch.ravel() - background)
        return design @ coeff + background - patch.ravel(), coeff[0]

    try:
        fitted = least_squares(lambda p: solve(p)[0], [x0, y0],
                               bounds=([x0 - 1, y0 - 1], [x0 + 1, y0 + 1]),
                               max_nfev=20)
        _, flux = solve(fitted.x)
        return float(fitted.x[0]), float(fitted.x[1]), float(flux)
    except Exception:
        return x0, y0, np.nan


def fit_catalogue_spatial(image, grid, xy, passes=2):
    """Two coordinate-refinement passes make neighbouring model positions consistent."""
    current = np.asarray(xy, float).copy()
    flux = np.full(len(current), np.nan)
    for _ in range(passes):
        tree = cKDTree(current)
        updated = np.empty_like(current)
        new_flux = np.empty(len(current))
        for i, pos in enumerate(current):
            ids = tree.query_ball_point(pos, r=6.0)
            neighbours = current[[j for j in ids if j != i]]
            updated[i, 0], updated[i, 1], new_flux[i] = fit_one_spatial(image, grid, pos[0], pos[1], neighbours)
        current, flux = updated, new_flux
    return current, flux


def run_cluster(cluster):
    image, cat = old.read_cluster(cluster)
    x, y, _, quality, _ = old.catalog_subsets(cat)
    qxy = np.c_[x[quality], y[quality]]
    qmag = np.asarray(cat["Vvega"], float)[quality]
    partition = old.ref_cells(qxy)
    sub, rms = base.estimate_background(image)
    src = base.detect_sources(sub, rms, fwhm=2.2, threshold_sigma=3.0)
    initial = np.c_[src["xcentroid"], src["ycentroid"]]
    global_psf, _ = epsf.build_epsf(sub, src)
    detections, _, _, _ = epsf.residual_candidates(sub, rms, global_psf, initial)
    grid = build_quadrant_psfs(sub, src)
    fitted, flux = fit_catalogue_spatial(sub, grid, detections)
    previous = json.loads((HERE / "hst_epsf_deblend_results" / "hst_epsf_deblend_summary.json").read_text(encoding="utf-8"))
    mag_zero = next(r["mag_zero_point"] for r in previous["results"] if r["cluster"] == cluster)
    metrics = epsf.evaluate(cluster, detections, fitted, flux, qxy, qmag, partition, mag_zero)
    metrics["psf_grid"] = {f"{ix},{iy}": {"stamps": n, "mode": mode} for (ix, iy), (_, n, mode) in grid.items()}
    return metrics


def main():
    OUT.mkdir(exist_ok=True)
    # Begin with the decisive crowded cluster.  This avoids spending time on
    # injections or the sparse NGC 6397 dense subset before merit is shown.
    result = run_cluster("ngc6752")
    payload = {"status": "NOT_FOR_MANUSCRIPT_UNTIL_REPLICATED_AND_IMPROVED",
               "method": "quadrant empirical PSF + two-pass neighbour-aware fitting",
               "cluster": "ngc6752", "result": result}
    (OUT / "ngc6752_spatial_epsf_joint_pilot.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
