#!/usr/bin/env python
"""Controlled F606W implementations of recent crowded-field method families.

This is not a claim of bit-for-bit reproduction of instrument pipelines.  It
compares methods that can be applied fairly to the same single ACSGGCT stacked
image: global empirical PSF, a three-Gaussian discrete-PSF approximation, and
the existing spatial empirical-PSF joint-fitting branch.
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import numpy as np
from scipy.optimize import nnls

from acsggct_expanded_high_density_precision import (
    CLUSTERS, epsf, high_density_metrics, imageops, load_cluster,
    spatial_epsf,
)
from common_match_high_density_precision import common_metrics

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results" / "hst_literature_method_benchmark"


def proposals_and_global_epsf(image, rms):
    sources = imageops.detect_sources(image, rms, fwhm=2.2, threshold_sigma=3.0)
    initial = np.c_[np.asarray(sources["xcentroid"], float), np.asarray(sources["ycentroid"], float)]
    global_psf, nstamps = epsf.build_epsf(image, sources)
    candidates, _, _, residual_added = epsf.residual_candidates(image, rms, global_psf, initial)
    return sources, global_psf, candidates, {"initial_proposals": int(len(initial)),
                                               "residual_candidates_added": int(residual_added),
                                               "global_epsf_stamps": int(nstamps)}


def three_gaussian_dpsf(psf):
    """Fit a non-negative 3-Gaussian radial mixture to an empirical PSF stamp.

    This captures the discrete-PSF/Multi-Gaussian core of Nie et al. (2025)
    under the same image-only, single-band restriction as the other branches.
    """
    half = (psf.shape[0] - 1) / 2
    yy, xx = np.indices(psf.shape, dtype=float)
    rr2 = (xx - half) ** 2 + (yy - half) ** 2
    sigmas = np.array([0.55, 1.20, 2.80])
    design = np.column_stack([np.exp(-0.5 * rr2.ravel() / s**2) for s in sigmas])
    coeff, _ = nnls(design, psf.ravel())
    model = (design @ coeff).reshape(psf.shape)
    model = np.maximum(model, 0)
    return model / model.sum(), {"gaussian_sigmas_px": sigmas.tolist(), "gaussian_weights": coeff.tolist()}


def run_cluster(cluster):
    raw, refs, mags, audit = load_cluster(cluster)
    image, rms = imageops.estimate_background(raw)
    sources, global_psf, candidates, provenance = proposals_and_global_epsf(image, rms)
    branches = {}
    started = time.perf_counter()
    xy, flux = epsf.fit_catalogue(image, global_psf, candidates)
    good = np.isfinite(xy).all(axis=1) & np.isfinite(flux) & (flux > 0)
    branches["global_epsf_joint"] = (xy[good], flux[good])
    runtime_global = time.perf_counter() - started
    started = time.perf_counter()
    mge_psf, mge_meta = three_gaussian_dpsf(global_psf)
    xy, flux = epsf.fit_catalogue(image, mge_psf, candidates)
    good = np.isfinite(xy).all(axis=1) & np.isfinite(flux) & (flux > 0)
    branches["three_gaussian_dpsf_joint"] = (xy[good], flux[good])
    runtime_mge = time.perf_counter() - started
    started = time.perf_counter()
    grid = spatial_epsf.build_quadrant_psfs(image, sources)
    xy, flux = spatial_epsf.fit_catalogue_spatial(image, grid, candidates, passes=2)
    good = np.isfinite(xy).all(axis=1) & np.isfinite(flux) & (flux > 0)
    branches["spatial_epsf_joint"] = (xy[good], flux[good])
    runtime_spatial = time.perf_counter() - started
    runtimes = {"global_epsf_joint": runtime_global, "three_gaussian_dpsf_joint": runtime_mge,
                "spatial_epsf_joint": runtime_spatial}
    rows = []
    for method, (xy, flux) in branches.items():
        row = high_density_metrics(xy, flux, refs, mags)
        rows.append({"cluster": cluster, "method": method, "candidates": int(len(xy)),
                     "runtime_s": float(runtimes[method]), **provenance,
                     **({"mge": mge_meta} if method == "three_gaussian_dpsf_joint" else {}), **row})
    pair_rows = []
    for comparator in ("global_epsf_joint", "three_gaussian_dpsf_joint"):
        for row in common_metrics(refs, mags, {comparator: branches[comparator], "spatial_epsf_joint": branches["spatial_epsf_joint"]}):
            pair_rows.append({"cluster": cluster, "comparison": f"{comparator}_vs_spatial_epsf_joint", **row})
    return rows, pair_rows, audit


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows, pairs, audits = [], [], {}
    for cluster in CLUSTERS:
        print(f"Running literature-method benchmark on {cluster}...", flush=True)
        field_rows, field_pairs, audit = run_cluster(cluster)
        rows.extend(field_rows); pairs.extend(field_pairs); audits[cluster] = audit
    (OUT / "summary.json").write_text(json.dumps({"protocol": {"scope": "single F606W stacked image; image-only source fitting",
        "literature_mapping": {"global_epsf_joint": "empirical/effective-PSF ablation (Libralato et al. 2024)",
        "three_gaussian_dpsf_joint": "three-Gaussian discrete-PSF approximation (Nie et al. 2025)",
        "spatial_epsf_joint": "spatial empirical-PSF and joint fitting"}}, "audits": audits, "results": rows, "same_star_pairs": pairs}, indent=2), encoding="utf-8")
    for name, data in (("summary.csv", rows), ("same_star_pairs.csv", pairs)):
        with (OUT / name).open("w", newline="", encoding="utf-8") as handle:
            fields = sorted({key for row in data for key in row}); writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(data)


if __name__ == "__main__":
    main()
