#!/usr/bin/env python
"""Common F606W ACSGC benchmark for additional dense Galactic globular clusters.

This extends the existing ACSGC protocol rather than pooling incompatible
catalogues: one central 1200-pixel crop, Anderson catalogue quality cuts,
2-pixel greedy association, spatially held-out measurement calibration, and
image-only DAO/SEP/Photutils/AstroCFR proposal branches.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.table import Table
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src" / "wpdc")); sys.path.insert(0, str(HERE))
import angst_non_globular_baseline as common
import candidate_features
import hst_unified_baseline_benchmark as baseline
import real_data_zero_shot_generalization as imageops

DATA = ROOT / "external" / "acsggct_expanded"
OUT = ROOT / "results" / "acsggct_expanded_dense"
CLUSTERS = ("ngc2808", "ngc5286", "ngc6388", "ngc6441")
METHODS = ("dao", "sep", "photutils_psf", "astrocfr_epsf", "astrocfr_photutils_hybrid")
LABELS = common.LABELS
CROP_SIZE = 1200


def load(cluster: str):
    prefix = f"hlsp_acsggct_hst_acs-wfc_{cluster}"
    image_path = DATA / f"{prefix}_f606w_v2_img.fits"
    cat_path = DATA / f"{prefix}_r.rdviq.cal.adj.zpt"
    image = fits.getdata(image_path).astype(float)
    cat = Table.read(cat_path, format="ascii")
    x0 = (image.shape[1] - CROP_SIZE) // 2
    y0 = (image.shape[0] - CROP_SIZE) // 2
    crop = image[y0:y0+CROP_SIZE, x0:x0+CROP_SIZE].copy()
    bad = ~np.isfinite(crop) | (crop <= -700)
    crop[bad] = np.nanmedian(crop[~bad])
    x = np.asarray(cat["x"], float) - 1.0 - x0
    y = np.asarray(cat["y"], float) - 1.0 - y0
    mag = np.asarray(cat["Vvega"], float)
    err = np.asarray(cat["err"], float)
    qfit = np.asarray(cat["qfitV"], float)
    oth = np.asarray(cat["othv"], float)
    nv = np.asarray(cat["Nv"], int)
    inside = (x >= 12) & (x < CROP_SIZE-12) & (y >= 12) & (y < CROP_SIZE-12)
    quality = (inside & np.isfinite(mag) & (mag < 90) & (err < .10) &
               (qfit < .30) & (oth < 1.0) & (nv >= 1))
    return crop, np.c_[x[quality], y[quality]], mag[quality], {
        "image": str(image_path), "catalogue": str(cat_path), "crop_origin_xy": [x0, y0],
        "quality_references": int(quality.sum()), "catalogue_total": int(len(cat))}


def evaluate(cluster, method, xy, flux, refs, mags, elapsed, memory):
    part = common.spatial_partition(refs)
    held = part == 2
    match, _ = common.one_to_one(xy, refs[held])
    k, n = int(match.sum()), int(held.sum())
    r = {"cluster": cluster, "method": method, "label": LABELS[method],
         "candidates": int(len(xy)), "test_references": n, "test_recovered": k,
         "catalogue_recovery": k/max(n,1), "catalogue_recovery_ci95": common.wilson(k,n),
         "runtime_s": float(elapsed), "runtime_s_per_mpix": float(elapsed / CROP_SIZE**2 * 1e6),
         "peak_rss_delta_mb": float(memory)}
    for limit in (18,20,22):
        sub = held & (mags <= limit); m,_ = common.one_to_one(xy, refs[sub])
        r[f"recovery_v_le_{limit}"] = float(m.sum()/max(sub.sum(),1)); r[f"n_v_le_{limit}"] = int(sub.sum())
    tree=cKDTree(refs); dens=np.asarray([len(tree.query_ball_point(p,10))-1 for p in refs])
    dense=held&(mags<=20)&(dens>=3); m,_=common.one_to_one(xy,refs[dense])
    r["dense_v20_n"]=int(dense.sum());r["dense_v20_recovery"]=float(m.sum()/max(dense.sum(),1))
    r.update(common.robust_measurements(xy,flux,refs,mags,part))
    return r


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--cluster",choices=CLUSTERS+("all",),default="all");parser.add_argument("--output-dir",type=Path,default=OUT);args=parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True); clusters=CLUSTERS if args.cluster=="all" else (args.cluster,); rows=[];audits={}
    for cluster in clusters:
        print("Loading",cluster,flush=True); raw,refs,mags,audit=load(cluster); image,rms=imageops.estimate_background(raw)
        bright=imageops.detect_sources(image,rms,fwhm=2,threshold_sigma=10)
        fwhm=float(np.clip(candidate_features.estimate_psf_fwhm(image,bright,rms,min_snr=20,max_sources=40),1.5,4.0))
        audit.update({"background_rms":float(rms),"image_only_fwhm_px":fwhm});audits[cluster]=audit
        for method in METHODS:
            print(cluster,method,flush=True)
            try:
                (xy,flux),sec,mem=common.measured(lambda: common.run_method(method,image,rms,fwhm))
                rows.append(evaluate(cluster,method,xy,flux,refs,mags,sec,mem))
            except Exception as exc: rows.append({"cluster":cluster,"method":method,"label":LABELS[method],"error":f"{type(exc).__name__}: {exc}"})
    payload={"protocol":{"input":"public ACSGC F606W reference image plus Anderson catalogue","scope":"catalogue-conditioned; not blind purity","quality":"err<0.10, qfitV<0.30, othv<1, Nv>=1","crop":"central 1200x1200","association_radius_px":2,"measurement":"non-test-stripe affine and zero point; held-out residuals"},"audits":audits,"results":rows}
    (args.output_dir/'summary.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    fields=sorted({k for r in rows for k in r})
    with (args.output_dir/'summary.csv').open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    print(json.dumps(rows,indent=2))

if __name__=='__main__':main()
