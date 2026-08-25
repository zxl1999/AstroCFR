#!/usr/bin/env python
"""Exploratory group-wise WPDC spatial-ePSF deblender.

Unlike the previous coordinate-at-a-time pilot, every source in a local blend
group is optimized together with a shared constant background.  The model is
still image-only: PSFs come from the four quadrant ePSF grid and reference
catalogues are touched only by the final benchmark evaluator.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial import cKDTree


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod
    spec.loader.exec_module(mod); return mod


def group_indices(xy, radius=7.0):
    """Connected components of the proposal graph (no catalogue labels)."""
    tree = cKDTree(xy); pairs = tree.query_pairs(radius)
    parent = np.arange(len(xy))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for a, b in pairs:
        ra, rb = root(a), root(b)
        if ra != rb: parent[rb] = ra
    groups = {}
    for i in range(len(xy)): groups.setdefault(root(i), []).append(i)
    return list(groups.values())


def fit_group(image, grid, epsf, xy, flux_seed, ids, radius=8):
    """Jointly fit x, y, positive flux and one shared local background."""
    p = np.asarray(xy[ids], float); n = len(p)
    cx, cy = np.mean(p, axis=0); half = int(max(6, min(10, radius + 2)))
    x0, y0 = int(round(cx)), int(round(cy))
    if x0-half < 0 or y0-half < 0 or x0+half >= image.shape[1] or y0+half >= image.shape[0]:
        return p, np.asarray(flux_seed[ids], float), np.nan
    patch = image[y0-half:y0+half+1, x0-half:x0+half+1]
    yy, xx = np.mgrid[y0-half:y0+half+1, x0-half:x0+half+1].astype(float)
    edge = np.r_[patch[0], patch[-1], patch[:, 0], patch[:, -1]]
    b0 = float(np.median(edge))
    seed = np.maximum(np.asarray(flux_seed[ids], float), 1.0)
    def psf_at(x, y):
        ix = int(np.clip(x * 2 // image.shape[1], 0, 1)); iy = int(np.clip(y * 2 // image.shape[0], 0, 1))
        return grid[(ix, iy)][0]
    def model(par):
        coords = par[:2*n].reshape(n, 2); flux = np.maximum(par[2*n:3*n], 0)
        out = np.full(patch.shape, par[-1], float)
        for (x, y), f in zip(coords, flux):
            out += f * epsf.psf_values(psf_at(x, y), xx, yy, x, y).reshape(patch.shape)
        return out
    par0 = np.r_[p.reshape(-1), seed, b0]
    low = np.r_[(p - 1.25).reshape(-1), np.zeros(n), b0 - 5 * np.nanstd(patch)]
    high = np.r_[(p + 1.25).reshape(-1), np.full(n, np.inf), b0 + 5 * np.nanstd(patch)]
    try:
        fit = least_squares(lambda q: (model(q) - patch).ravel(), par0, bounds=(low, high),
                            loss="soft_l1", f_scale=max(float(np.nanmedian(np.abs(patch-b0))), 1.0),
                            max_nfev=35, x_scale="jac")
        out = fit.x; coords = out[:2*n].reshape(n, 2); flux = np.maximum(out[2*n:3*n], 0)
        resid = float(np.sqrt(np.mean((model(out) - patch) ** 2)))
        return coords, flux, resid
    except Exception:
        return p, seed, np.nan


def run(args):
    work = Path(args.work_dir); outdir = Path(args.output_dir); outdir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(work.resolve()))
    old = load(work / "hst_acsggct_benchmark.py", "group_old")
    base = load(work / "real_data_zero_shot_generalization.py", "group_base")
    epsf = load(work / "hst_epsf_deblend_artificial_stars.py", "group_epsf")
    spatial = load(work / "hst_spatial_epsf_joint_pilot.py", "group_spatial")
    image, _ = old.read_cluster(args.cluster); sub, rms = base.estimate_background(image)
    src = base.detect_sources(sub, rms, fwhm=2.2, threshold_sigma=3.0)
    initial = np.column_stack([np.asarray(src["xcentroid"], float), np.asarray(src["ycentroid"], float)])
    global_psf, _ = epsf.build_epsf(sub, src)
    candidates, _, _, added = epsf.residual_candidates(sub, rms, global_psf, initial)
    grid = spatial.build_quadrant_psfs(sub, src)
    seeds = np.array([max(float(sub[int(round(y)), int(round(x))]), 1.0) if 0 <= int(round(x)) < sub.shape[1] and 0 <= int(round(y)) < sub.shape[0] else 1.0 for x, y in candidates])
    groups = group_indices(candidates, radius=7.0)
    fitted = np.empty_like(candidates); flux = np.empty(len(candidates)); residual = np.full(len(candidates), np.nan)
    t0 = time.perf_counter()
    for ids in groups:
        # Preserve the established fast WPDC update for isolated proposals;
        # the new joint optimizer is exercised only where a genuine blend group
        # exists.  This keeps the comparison a deployable system experiment.
        if len(ids) == 1:
            xx, yy, ff0 = spatial.fit_one_spatial(sub, grid, candidates[ids[0], 0], candidates[ids[0], 1], np.empty((0, 2)))
            xy, ff, rr = np.asarray([[xx, yy]]), np.asarray([ff0]), np.nan
        else:
            xy, ff, rr = fit_group(sub, grid, epsf, candidates, seeds, ids)
        fitted[ids] = xy; flux[ids] = ff; residual[ids] = rr
    elapsed = time.perf_counter() - t0
    # Use the established evaluator for identical matching and measurement rules.
    _, cat = old.read_cluster(args.cluster); x, y, _, quality, _ = old.catalog_subsets(cat)
    qxy = np.column_stack([x[quality], y[quality]]); qmag = np.asarray(cat["Vvega"], float)[quality]
    part = old.ref_cells(qxy)
    fit_global = fitted + np.array([old.CROP_X0, old.CROP_Y0])
    test = old.adapt.cell_ids(fitted, 200) == 2; testref = part == 2
    match, _ = old.one_to_one(fit_global[test], qxy[testref])
    dense_tree = cKDTree(qxy); density = np.array([len(dense_tree.query_ball_point(p, 10))-1 for p in qxy])
    dense = testref & (qmag <= 20) & (density >= 3); dm, _ = old.one_to_one(fit_global[test], qxy[dense])
    metrics = {"cluster": args.cluster, "method": "wpdc_group_joint_spatial_epsf", "candidates": int(len(candidates)), "groups": int(len(groups)), "multi_source_groups": int(sum(len(g)>1 for g in groups)), "test_references": int(testref.sum()), "test_recovered": int(match.sum()), "test_completeness": float(match.sum()/max(testref.sum(),1)), "recall_v_le_20": float(old.one_to_one(fit_global[test], qxy[testref & (qmag<=20)])[0].sum()/max((testref & (qmag<=20)).sum(),1)), "high_density_v20_recall": float(dm.sum()/max(dense.sum(),1)), "high_density_v20_n": int(dense.sum()), "runtime_s": elapsed, "runtime_s_per_mpix": elapsed/(old.CROP_SIZE**2)*1e6, "median_group_residual": float(np.nanmedian(residual)), "residual_candidates_added": int(added)}
    good = np.isfinite(flux) & (flux > 0)
    metrics.update(old.measurement_metrics(fit_global, flux, good, qxy, qmag, part))
    payload = {"protocol": {"crop": "same central 1200x1200", "association_radius_px": 2, "test_partition": 2, "group_radius_px": 7, "model": "quadrant image-only ePSF + shared local background + joint nonnegative flux/coordinate fit", "status": "exploratory"}, "result": metrics}
    (outdir / f"{args.cluster}_group_joint_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--work-dir", required=True); parser.add_argument("--output-dir", required=True); parser.add_argument("--cluster", default="ngc6752"); run(parser.parse_args())
