#!/usr/bin/env python
"""Fair high-density comparison on the intersection of method matches."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from acsggct_expanded_high_density_precision import (
    CLUSTERS, PIXEL_SCALE_MAS, baseline, common, estimate_fwhm, high_density_metrics,
    load_cluster, photutils_psf, spatial_epsf_joint,
)
import real_data_zero_shot_generalization as imageops

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "results" / "acsggct_expanded_high_density_precision"


def method_matches(xy, flux, refs):
    matched, ri = common.one_to_one(xy, refs)
    ci = np.flatnonzero(matched)
    return {int(r): int(c) for c, r in zip(ci, ri[matched])}


def common_metrics(refs, mags, outputs):
    tree = cKDTree(refs)
    neighbours = np.asarray([len(tree.query_ball_point(p, 10.0)) - 1 for p in refs])
    dense = (mags <= 20) & (neighbours >= 3)
    partition = common.spatial_partition(refs)
    maps = {m: method_matches(xy, flux, refs) for m, (xy, flux) in outputs.items()}
    common_test = [r for r in maps[list(maps)[0]] if all(r in mm for mm in maps.values())
                   and partition[r] == 2 and dense[r]]
    rows = []
    for method, (xy, flux) in outputs.items():
        mm = maps[method]
        train_refs = [r for r in mm if partition[r] != 2]
        test_refs = common_test
        result = {"method": method, "common_matched_test_dense_n": len(test_refs),
                  "common_matched_non_test_n": len(train_refs)}
        if len(train_refs) < 10 or len(test_refs) < 5:
            rows.append({**result, "status": "insufficient common matches"})
            continue
        train_c = np.asarray([mm[r] for r in train_refs], int)
        train_r = np.asarray(train_refs, int)
        test_c = np.asarray([mm[r] for r in test_refs], int)
        test_r = np.asarray(test_refs, int)
        affine = baseline.old.fit_affine(xy[train_c], refs[train_r])
        delta = baseline.old.apply_affine(xy[test_c], affine) - refs[test_r]
        radial = np.sqrt(np.sum(delta ** 2, axis=1))
        med = np.median(radial); mad = 1.4826 * np.median(np.abs(radial - med))
        keep = radial <= med + max(3 * mad, 0.05)
        pos_px = np.sqrt(np.mean(np.sum(delta[keep] ** 2, axis=1) / 2.0))
        instrumental = -2.5 * np.log10(np.maximum(np.asarray(flux, float), 1e-6))
        zp = np.median(mags[train_r] - instrumental[train_c])
        residual = instrumental[test_c] + zp - mags[test_r]
        med = np.median(residual); mad = 1.4826 * np.median(np.abs(residual - med))
        keep_p = np.abs(residual - med) <= max(3 * mad, 0.03)
        mag_rms = np.sqrt(np.mean((residual[keep_p] - np.mean(residual[keep_p])) ** 2))
        rows.append({**result, "status": "ok", "astrometric_rms_mas": float(pos_px * PIXEL_SCALE_MAS),
                     "photometric_rms_mag": float(mag_rms), "astrometric_retained_n": int(keep.sum()),
                     "photometric_retained_n": int(keep_p.sum())})
    return rows


def run(cluster):
    raw, refs, mags, audit = load_cluster(cluster)
    image, rms = imageops.estimate_background(raw)
    fwhm = estimate_fwhm(image, rms)
    outputs = {}
    xy, flux = photutils_psf(image, rms, fwhm); outputs["photutils_psf"] = (xy, flux)
    xy, flux, _ = spatial_epsf_joint(image, rms); outputs["astrocfr_spatial_epsf_joint"] = (xy, flux)
    rows = common_metrics(refs, mags, outputs)
    for row in rows: row["cluster"] = cluster
    return rows


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for cluster in CLUSTERS:
        print(f"Running common-match audit on {cluster}...", flush=True)
        rows.extend(run(cluster))
    (OUTPUT / "common_match_summary.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    with (OUTPUT / "common_match_summary.csv").open("w", newline="", encoding="utf-8") as f:
        fields = sorted({k for r in rows for k in r}); w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
