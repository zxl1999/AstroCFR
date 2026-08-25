#!/usr/bin/env python
"""Audit detector-threshold and association-radius sensitivity on NGC 6752.

The association radius is varied only after every catalogue has been produced;
it is never used to tune a method. The detector-threshold scan concerns the
common image-only DAO-style proposal front end, not a final catalogue claim.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results" / "hst_parameter_sensitivity"


def upstream_path(value: str | None) -> Path:
    candidate = value or os.environ.get("ASTROCFR_UPSTREAM")
    if not candidate:
        raise SystemExit("Provide --upstream PATH or set ASTROCFR_UPSTREAM.")
    path = Path(candidate).expanduser().resolve()
    if not (path / "hst_unified_baseline_benchmark.py").exists():
        raise SystemExit(f"No upstream benchmark in {path}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream")
    parser.add_argument("--cluster", default="ngc6752")
    args = parser.parse_args()
    upstream = upstream_path(args.upstream)
    sys.path.insert(0, str(upstream))
    import hst_acsggct_benchmark as hst
    import hst_unified_baseline_benchmark as bench
    import real_data_domain_adaptation as adapt
    import real_data_zero_shot_generalization as base

    # The public ACSGGCT images are deliberately kept outside the Git tree.
    # Resolve the shared research-data copy when this script is run from the
    # reproducibility checkout, rather than silently falling back to a stale
    # or partial local directory.
    if not hst.DATA.exists():
        shared = next((p for p in ROOT.parent.glob("CSST_*")
                       if (p / "代码及中间过程文件" / "fanhuaxing" / "real_data_hst_acsggct").exists()), None)
        if shared is None:
            raise SystemExit("ACSGGCT data directory is unavailable; provide the shared research-data copy.")
        hst.DATA = shared / "代码及中间过程文件" / "fanhuaxing" / "real_data_hst_acsggct"

    image, catalogue = hst.read_cluster(args.cluster)
    subtracted, rms = base.estimate_background(image)
    preliminary = base.detect_sources(subtracted, rms, fwhm=2.0, threshold_sigma=10.0)
    module = adapt.load_pipeline()
    fwhm = float(np.clip(module.estimate_psf_fwhm(subtracted, preliminary, rms,
                                                  min_snr=20, max_sources=40), 1.5, 4.0))
    # This audit concerns image-only detector, association, and PSF-fitting
    # robustness.  It deliberately omits the archival RF branch so that it
    # does not require the non-distributed simulation feature cache.
    rf_context = None
    x, y, measured, quality, _ = hst.catalog_subsets(catalogue)
    measured_xy = np.c_[x[measured], y[measured]]
    quality_xy = np.c_[x[quality], y[quality]]
    quality_test = hst.ref_cells(quality_xy) == 2

    threshold_rows = []
    for sigma in (2.5, 3.0, 3.5, 4.0):
        sources = base.detect_sources(subtracted, rms, fwhm=fwhm, threshold_sigma=sigma)
        xcol, ycol = module._xy_columns(sources)
        local_xy = np.c_[np.asarray(sources[xcol], float), np.asarray(sources[ycol], float)]
        test = adapt.cell_ids(local_xy, 200) == 2
        global_test = local_xy[test] + np.array([hst.CROP_X0, hst.CROP_Y0])
        recovered, _ = base.greedy_match(global_test, quality_xy[quality_test], 2.0)
        catalogue_match, _ = base.greedy_match(global_test, measured_xy, 2.0)
        threshold_rows.append({"threshold_sigma": sigma, "test_candidates": int(test.sum()),
                               "quality_test_references": int(quality_test.sum()),
                               "quality_recovered": int(recovered.sum()),
                               "quality_completeness": float(recovered.sum()/max(quality_test.sum(), 1)),
                               "catalogue_match_lower_bound": float(catalogue_match.sum()/max(test.sum(), 1))})

    radius_rows = []
    for method in ("dao", "sep", "photutils_psf", "wpdc_epsf_deblend", "wpdc_spatial_epsf_joint"):
        xy, _ = bench.method_run(method, subtracted, rms, fwhm, rf_context)
        test = adapt.cell_ids(xy, 200) == 2
        global_test = xy[test] + np.array([hst.CROP_X0, hst.CROP_Y0])
        for radius in (1.0, 1.5, 2.0, 2.5, 3.0):
            recovered, _ = base.greedy_match(global_test, quality_xy[quality_test], radius)
            catalogue_match, _ = base.greedy_match(global_test, measured_xy, radius)
            radius_rows.append({"method": method, "label": bench.LABEL[method],
                                "association_radius_px": radius,
                                "test_candidates": int(test.sum()),
                                "quality_test_references": int(quality_test.sum()),
                                "quality_recovered": int(recovered.sum()),
                                "quality_completeness": float(recovered.sum()/max(quality_test.sum(), 1)),
                                "catalogue_match_lower_bound": float(catalogue_match.sum()/max(test.sum(), 1))})

    payload = {"protocol": {"cluster": args.cluster, "crop": "central 1200x1200 ACSGGCT F606W",
                            "spatial_test_partition": 2,
                            "threshold_scan": "common image-only DAO-style proposal front end; fixed 2-pixel evaluation",
                            "radius_scan": "catalogues fixed before evaluation; radius is not a tuning parameter",
                            "purity_warning": "catalogue-match rate is a lower bound, not blind purity"},
               "estimated_fwhm_px": fwhm,
               "detection_threshold_scan": threshold_rows,
               "association_radius_scan": radius_rows}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "parameter_sensitivity_audit.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
