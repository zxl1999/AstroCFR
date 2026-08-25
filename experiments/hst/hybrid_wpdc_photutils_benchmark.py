#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Evaluate a WPDC-candidate / Photutils-measurement hybrid branch.

WPDC supplies image-only ePSF residual-deblended candidate positions. Those
positions and conservative flux seeds are passed to Photutils PSFPhotometry;
Photutils therefore contributes the final Gaussian-PSF measurement rather
than its DAOStarFinder proposal frontend. The same ACSGGCT crop, association
radius, spatial test partition and reference-quality rule are used by the
existing controlled benchmark.
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
from photutils.psf import CircularGaussianPRF, PSFPhotometry, SourceGrouper


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); sys.modules[name] = module; spec.loader.exec_module(module)
    return module


def evaluate_local(old, adapt, cluster, name, xy, flux, elapsed):
    """Reuse the controlled HST metrics without depending on benchmark version."""
    _, cat = old.read_cluster(cluster); x, y, measured, quality, _ = old.catalog_subsets(cat)
    ref = np.column_stack([x[quality], y[quality]]); mag = np.asarray(cat["Vvega"], float)[quality]
    part = old.ref_cells(ref); det = xy + np.array([old.CROP_X0, old.CROP_Y0])
    test = adapt.cell_ids(xy, 200) == 2; testref = part == 2
    matched, _ = old.one_to_one(det[test], ref[testref])
    allref = np.column_stack([x[measured], y[measured]])
    allmatched, _ = old.one_to_one(det[test], allref)
    result = {"cluster": cluster, "method": name, "candidates": int(len(xy)), "test_references": int(testref.sum()),
              "test_recovered": int(matched.sum()), "test_completeness": float(matched.sum() / max(testref.sum(), 1)),
              "test_catalog_match_lower_bound": float(allmatched.sum() / max(test.sum(), 1)), "runtime_s": float(elapsed),
              "runtime_s_per_mpix": float(elapsed / (old.CROP_SIZE ** 2) * 1e6)}
    for limit in (18, 20, 22):
        subset = testref & (mag <= limit); m, _ = old.one_to_one(det[test], ref[subset])
        result[f"recall_v_le_{limit}"] = float(m.sum() / max(subset.sum(), 1)); result[f"n_v_le_{limit}"] = int(subset.sum())
    tree = __import__("scipy").spatial.cKDTree(ref)
    density = np.array([len(tree.query_ball_point(point, 10)) - 1 for point in ref])
    dense = testref & (mag <= 20) & (density >= 3); m, _ = old.one_to_one(det[test], ref[dense])
    result["high_density_v20_recall"] = float(m.sum() / max(dense.sum(), 1)); result["high_density_v20_n"] = int(dense.sum())
    result.update(old.measurement_metrics(det, flux, np.ones(len(det), bool), ref, mag, part))
    return result


def run_hybrid(old, base, adapt, epsf, cluster):
    image, _ = old.read_cluster(cluster)
    sub, rms = base.estimate_background(image)
    pre = base.detect_sources(sub, rms, fwhm=2.0, threshold_sigma=10.0)
    mod = adapt.load_pipeline()
    fwhm = float(np.clip(mod.estimate_psf_fwhm(sub, pre, rms, min_snr=20, max_sources=40), 1.5, 4.0))
    sources = base.detect_sources(sub, rms, fwhm=fwhm, threshold_sigma=3.0)
    initial = np.column_stack([np.asarray(sources["xcentroid"], float), np.asarray(sources["ycentroid"], float)])
    psf, _ = epsf.build_epsf(sub, sources)
    candidates, _, _, _ = epsf.residual_candidates(sub, rms, psf, initial)
    if len(candidates) == 0:
        raise RuntimeError(f"No WPDC candidates for {cluster}")
    flux_seed = []
    for x, y in candidates:
        ix, iy = int(round(x)), int(round(y))
        if 0 <= ix < sub.shape[1] and 0 <= iy < sub.shape[0]: flux_seed.append(max(float(sub[iy, ix]), 1.0))
        else: flux_seed.append(1.0)
    init = Table(); init["x_0"] = candidates[:, 0]; init["y_0"] = candidates[:, 1]; init["flux_0"] = np.asarray(flux_seed)
    phot = PSFPhotometry(CircularGaussianPRF(fwhm=fwhm), fit_shape=(9, 9), finder=None,
                         grouper=SourceGrouper(min_separation=2.0), aperture_radius=3.0,
                         fitter_maxiters=30, group_warning_threshold=1000, progress_bar=False)
    t0 = time.perf_counter(); table = phot(sub, init_params=init); elapsed = time.perf_counter() - t0
    good = np.isfinite(table["x_fit"]) & np.isfinite(table["y_fit"]) & np.isfinite(table["flux_fit"]) & (table["flux_fit"] > 0)
    xy = np.column_stack([np.asarray(table["x_fit"][good], float), np.asarray(table["y_fit"][good], float)])
    flux = np.asarray(table["flux_fit"][good], float)
    metrics = evaluate_local(old, adapt, cluster, "wpdc_photutils_hybrid", xy, flux, elapsed)
    metrics.update({"fwhm_px": fwhm, "wpdc_candidates": int(len(candidates)), "photutils_retained": int(len(xy)),
                    "measurement": "Photutils PSFPhotometry initialized by WPDC ePSF+residual candidates"})
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(); args.output_dir.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.work_dir.resolve()))
    old = load(args.work_dir / "hst_acsggct_benchmark.py", "hybrid_old")
    base = load(args.work_dir / "real_data_zero_shot_generalization.py", "hybrid_base")
    adapt = load(args.work_dir / "real_data_domain_adaptation.py", "hybrid_adapt")
    epsf = load(args.work_dir / "hst_epsf_deblend_artificial_stars.py", "hybrid_epsf")
    results = []
    for cluster in old.CLUSTERS:
        print(f"Hybrid benchmark: {cluster}", flush=True)
        results.append(run_hybrid(old, base, adapt, epsf, cluster))
        print(json.dumps(results[-1], indent=2), flush=True)
    payload = {"protocol": {"candidate_stage": "WPDC ePSF + residual deblend", "measurement_stage": "Photutils PSFPhotometry", "fit_shape": [9, 9], "association_radius_px": 2, "spatial_test_partition": 2}, "results": results}
    (args.output_dir / "hybrid_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    fields = sorted({k for row in results for k in row})
    with (args.output_dir / "hybrid_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(results)
    lines = ["# WPDC candidate generation + Photutils measurement hybrid", "",
             "WPDC ePSF/residual deblending provides candidate positions; Photutils PSFPhotometry performs the final Gaussian-PSF fit from those initial positions. This is a measurement hybrid, not a new purity classifier. All metrics use the existing ACSGGCT crop and untouched spatial test partition.", "",
             "| Cluster | WPDC candidates | Photutils fitted | Test completeness | Dense V<=20 recall | Position RMS / mas | Magnitude RMS / mag | Runtime / s |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        lines.append(f"| {r['cluster']} | {r['wpdc_candidates']} | {r['photutils_retained']} | {r['test_completeness']:.3f} | {r['high_density_v20_recall']:.3f} | {r.get('astrometric_rms_mas', float('nan')):.3f} | {r.get('photometric_rms_mag', float('nan')):.3f} | {r['runtime_s']:.2f} |")
    lines += ["", "Interpretation: the hybrid is useful only if it retains WPDC's crowded-field recovery while reducing measurement RMS. It must be compared against both WPDC ePSF+deblend and standalone Photutils; no universal advantage is assumed."]
    (args.output_dir / "hybrid_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(args.output_dir)


if __name__ == "__main__": main()
