#!/usr/bin/env python
"""Run the common single-image baseline suite on all 11 ACSGGCT fields.

The original three fields live in the recovered ``fanhuaxing`` package while
the eight expanded fields live in ``external/acsggct_expanded``.  This wrapper
keeps the benchmark code unchanged and only supplies a path-aware loader.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src" / "wpdc"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import hst_acsggct_benchmark as old
import hst_unified_baseline_benchmark as bench
from astropy.io import fits
from astropy.table import Table
import numpy as np

EXPANDED = ROOT / "external" / "acsggct_expanded"
RECOVERED = (ROOT.parent / "CSST_上海电机学院" / "代码及中间过程文件" /
             "fanhuaxing" / "real_data_hst_acsggct")
CLUSTERS = ("ngc2808", "ngc5286", "ngc6388", "ngc6441", "ngc0104",
            "ngc0362", "ngc6093", "ngc6624", "ngc6397", "ngc6752",
            "ngc1851")


def data_root(cluster: str) -> Path:
    root = RECOVERED if cluster in {"ngc6397", "ngc6752", "ngc1851"} else EXPANDED
    return root


def read_cluster(cluster: str):
    root = data_root(cluster)
    prefix = f"hlsp_acsggct_hst_acs-wfc_{cluster}"
    image = fits.getdata(root / f"{prefix}_f606w_v2_img.fits").astype(float)
    cat = Table.read(root / f"{prefix}_r.rdviq.cal.adj.zpt", format="ascii")
    crop = image[old.CROP_Y0:old.CROP_Y0 + old.CROP_SIZE,
                 old.CROP_X0:old.CROP_X0 + old.CROP_SIZE].copy()
    bad = (~np.isfinite(crop)) | (crop <= -700)
    crop[bad] = np.nanmedian(crop[~bad])
    return crop, cat


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "results" / "acsggct_all11_baselines")
    parser.add_argument("--skip-artificial", action="store_true")
    args = parser.parse_args()
    # All benchmark helper functions call old.read_cluster, so patching this
    # single loader is sufficient and does not alter any metric implementation.
    old.read_cluster = read_cluster
    old.CLUSTERS = CLUSTERS
    # The frozen simulation RF feature cache is intentionally not distributed
    # in this workspace.  Run every image-only branch here and record RF as a
    # separate unavailable method rather than silently fabricating a model.
    bench.METHODS = tuple(m for m in bench.METHODS if m != "wpdc_rf")
    bench.prepare_wpdc_rf = lambda *args, **kwargs: None
    bench.main_args = args
    # Reuse the original driver by emulating its argparse namespace.  The
    # driver reads old.CLUSTERS and writes the requested output directory.
    import contextlib
    import io
    old_argv = sys.argv
    sys.argv = [str(HERE / "hst_unified_baseline_benchmark.py"),
                "--output-dir", str(args.output_dir)]
    if args.skip_artificial:
        sys.argv.append("--skip-artificial")
    try:
        bench.main()
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    main()
