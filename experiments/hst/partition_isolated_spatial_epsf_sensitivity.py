#!/usr/bin/env python
"""Spatially disjoint ePSF-construction sensitivity audit for AstroCFR.

Candidates are detected from the full science crop exactly as in the primary
single-image branch.  In the sensitivity branch, however, only image-only DAO
proposals in spatial cells 0/1 may supply empirical-PSF stamps.  Cell 2 is
never used in either global or quadrant ePSF construction, but remains the
held-out scoring partition.  Catalogue entries are used only for the existing
post-fit registration, zero point, and scoring steps.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src" / "wpdc"
for path in (HERE, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import hst_acsggct_benchmark as old
import hst_epsf_deblend_artificial_stars as epsf
import hst_spatial_epsf_joint_pilot as spatial
import real_data_zero_shot_generalization as imageops
import real_data_domain_adaptation as adapt
import run_acsggct_all11_baselines as loader


CLUSTERS = loader.CLUSTERS
BASELINE = ROOT / "results" / "acsggct_all11_baselines" / "hst_unified_baseline_summary.json"
DEFAULT_OUT = ROOT / "results" / "partition_isolated_spatial_epsf_sensitivity"


def spatial_epsf_from_non_test_stamps(image: np.ndarray, rms: float) -> tuple[np.ndarray, np.ndarray, dict]:
    """Fit all image candidates, with ePSF stamps restricted to cells 0/1."""
    sources = imageops.detect_sources(image, rms, fwhm=2.2, threshold_sigma=3.0)
    if len(sources) == 0:
        return np.empty((0, 2)), np.empty(0), {"initial_proposals": 0, "stamp_proposals": 0}
    xy = np.c_[np.asarray(sources["xcentroid"], float), np.asarray(sources["ycentroid"], float)]
    stamp_mask = adapt.cell_ids(xy, 200) != 2
    stamp_sources = sources[stamp_mask]
    if len(stamp_sources) < 10:
        raise RuntimeError(f"Only {len(stamp_sources)} non-test DAO proposals available for ePSF stamps")
    global_psf, global_stamps = epsf.build_epsf(image, stamp_sources)
    candidates, _, _, residual_added = epsf.residual_candidates(image, rms, global_psf, xy)
    grid = spatial.build_quadrant_psfs(image, stamp_sources)
    fitted, flux = spatial.fit_catalogue_spatial(image, grid, candidates, passes=2)
    good = np.isfinite(fitted).all(axis=1) & np.isfinite(flux) & (flux > 0)
    return fitted[good], flux[good], {
        "initial_proposals": int(len(sources)),
        "stamp_proposals": int(len(stamp_sources)),
        "stamp_fraction": float(len(stamp_sources) / len(sources)),
        "global_epsf_stamps": int(global_stamps),
        "residual_candidates_added": int(residual_added),
        "quadrant_psf_stamps": {f"{ix},{iy}": int(item[1]) for (ix, iy), item in grid.items()},
        "quadrant_psf_mode": {f"{ix},{iy}": item[2] for (ix, iy), item in grid.items()},
    }


def run_cluster(cluster: str) -> dict:
    image, catalog = loader.read_cluster(cluster)
    subtracted, rms = imageops.estimate_background(image)
    started = time.perf_counter()
    xy, flux, provenance = spatial_epsf_from_non_test_stamps(subtracted, rms)

    # Use the primary benchmark's existing scoring implementation.  It uses
    # references only after fitting, and its registration/zero point exclude
    # the test partition.
    import hst_unified_baseline_benchmark as benchmark
    metrics = benchmark.evaluate(cluster, "wpdc_spatial_epsf_joint", xy, flux,
                                 time.perf_counter() - started, 0.0)
    return {
        "cluster": cluster,
        "method": "spatial_epsf_joint_non_test_stamps",
        "label": "AstroCFR spatial-ePSF joint fit (PSF stamps: partitions 0/1 only)",
        "background_rms": float(rms),
        "runtime_s": float(time.perf_counter() - started),
        **provenance,
        **metrics,
    }


def summarize(rows: list[dict]) -> dict:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["results"]
    primary = {r["cluster"]: r for r in baseline if r["method"] == "wpdc_spatial_epsf_joint"}
    comparison = []
    for row in rows:
        old_row = primary[row["cluster"]]
        comparison.append({
            "cluster": row["cluster"],
            "dense_n": row["high_density_v20_n"],
            "primary_dense_recovery": old_row["high_density_v20_recall"],
            "non_test_stamp_dense_recovery": row["high_density_v20_recall"],
            "delta_dense_recovery_pp": 100 * (row["high_density_v20_recall"] - old_row["high_density_v20_recall"]),
            "primary_position_rms_mas": old_row["astrometric_rms_mas"],
            "non_test_stamp_position_rms_mas": row["astrometric_rms_mas"],
            "delta_position_rms_mas": row["astrometric_rms_mas"] - old_row["astrometric_rms_mas"],
            "primary_magnitude_rms_mag": old_row["photometric_rms_mag"],
            "non_test_stamp_magnitude_rms_mag": row["photometric_rms_mag"],
            "delta_magnitude_rms_mag": row["photometric_rms_mag"] - old_row["photometric_rms_mag"],
        })
    return {
        "protocol": {
            "candidate_detection": "full image, image-only",
            "global_and_quadrant_epsf_stamps": "DAO proposals from spatial cells 0/1 only",
            "candidate_fitting": "full-image candidates, two-pass spatial ePSF neighbour fitting",
            "scoring": "partition-2 external catalogue references only; affine registration and zero point use non-test matches",
            "scope": "sensitivity audit, not a replacement primary branch",
        },
        "results": rows,
        "comparison_to_primary": comparison,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clusters", nargs="+", choices=CLUSTERS, default=list(CLUSTERS))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Patch all legacy benchmark helpers to use the same 11-field loader.
    old.read_cluster = loader.read_cluster
    rows = []
    for cluster in args.clusters:
        print(f"Running non-test-stamp ePSF sensitivity: {cluster}", flush=True)
        row = run_cluster(cluster)
        rows.append(row)
        (args.output_dir / f"{cluster}.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
    payload = summarize(rows)
    (args.output_dir / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    fields = sorted({k for row in payload["comparison_to_primary"] for k in row})
    with (args.output_dir / "comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(payload["comparison_to_primary"])
    print(json.dumps(payload["comparison_to_primary"], indent=2))


if __name__ == "__main__":
    main()
