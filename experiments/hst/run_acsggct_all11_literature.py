#!/usr/bin/env python
"""Run the reproducible global-ePSF and three-Gaussian dPSF ablations on all 11 fields."""
from __future__ import annotations

import sys
from pathlib import Path
import numpy as np
from astropy.io import fits
from astropy.table import Table

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src" / "wpdc"
for p in (SRC, HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import hst_acsggct_benchmark as old
import literature_method_benchmark as lit

EXPANDED = ROOT / "external" / "acsggct_expanded"
RECOVERED = (ROOT.parent / "CSST_上海电机学院" / "代码及中间过程文件" /
             "fanhuaxing" / "real_data_hst_acsggct")
CLUSTERS = ("ngc2808", "ngc5286", "ngc6388", "ngc6441", "ngc0104",
            "ngc0362", "ngc6093", "ngc6624", "ngc6397", "ngc6752",
            "ngc1851")


def load_cluster(cluster):
    root = RECOVERED if cluster in {"ngc6397", "ngc6752", "ngc1851"} else EXPANDED
    prefix = f"hlsp_acsggct_hst_acs-wfc_{cluster}"
    image = fits.getdata(root / f"{prefix}_f606w_v2_img.fits").astype(float)
    cat = Table.read(root / f"{prefix}_r.rdviq.cal.adj.zpt", format="ascii")
    crop = image[old.CROP_Y0:old.CROP_Y0 + old.CROP_SIZE,
                 old.CROP_X0:old.CROP_X0 + old.CROP_SIZE].copy()
    bad = (~np.isfinite(crop)) | (crop <= -700)
    crop[bad] = np.nanmedian(crop[~bad])
    x = np.asarray(cat["x"], float) - 1.0 - old.CROP_X0
    y = np.asarray(cat["y"], float) - 1.0 - old.CROP_Y0
    mag = np.asarray(cat["Vvega"], float)
    err = np.asarray(cat["err"], float)
    qfit = np.asarray(cat["qfitV"], float)
    oth = np.asarray(cat["othv"], float)
    nv = np.asarray(cat["Nv"], int)
    inside = (x >= 12) & (x < old.CROP_SIZE - 12) & (y >= 12) & (y < old.CROP_SIZE - 12)
    quality = inside & np.isfinite(mag) & (mag < 90) & (err < .10) & (qfit < .30) & (oth < 1.) & (nv >= 1)
    return crop, np.c_[x[quality], y[quality]], mag[quality], {"image": str(root / f"{prefix}_f606w_v2_img.fits"), "catalogue": str(root / f"{prefix}_r.rdviq.cal.adj.zpt")}


def main():
    def old_read_cluster(cluster):
        root = RECOVERED if cluster in {"ngc6397", "ngc6752", "ngc1851"} else EXPANDED
        prefix = f"hlsp_acsggct_hst_acs-wfc_{cluster}"
        image = fits.getdata(root / f"{prefix}_f606w_v2_img.fits").astype(float)
        crop = image[old.CROP_Y0:old.CROP_Y0 + old.CROP_SIZE,
                     old.CROP_X0:old.CROP_X0 + old.CROP_SIZE].copy()
        bad = (~np.isfinite(crop)) | (crop <= -700)
        crop[bad] = np.nanmedian(crop[~bad])
        cat = Table.read(root / f"{prefix}_r.rdviq.cal.adj.zpt", format="ascii")
        return crop, cat
    old.read_cluster = old_read_cluster
    lit.load_cluster = load_cluster
    lit.CLUSTERS = CLUSTERS
    lit.OUT = ROOT / "results" / "hst_literature_method_benchmark_all11"
    lit.OUT.mkdir(parents=True, exist_ok=True)
    rows, pairs, audits = [], [], {}
    for cluster in CLUSTERS:
        print(f"Running literature methods on {cluster}...", flush=True)
        field_rows, field_pairs, audit = lit.run_cluster(cluster)
        rows.extend(field_rows); pairs.extend(field_pairs); audits[cluster] = audit
    import json, csv
    payload = {"protocol": {"scope": "single F606W stacked image; image-only fitting", "fields": list(CLUSTERS)}, "audits": audits, "results": rows, "same_star_pairs": pairs}
    (lit.OUT / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for name, data in (("summary.csv", rows), ("same_star_pairs.csv", pairs)):
        fields = sorted({k for row in data for k in row})
        with (lit.OUT / name).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(data)


if __name__ == "__main__":
    main()
