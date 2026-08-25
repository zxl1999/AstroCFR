#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WPDC benchmark on two real HST/ACS globular-cluster fields."""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits
from astropy.table import Table
from scipy.spatial import cKDTree

import real_data_zero_shot_generalization as base
import real_data_domain_adaptation as adapt


HERE = Path(__file__).resolve().parent
DATA = HERE / "real_data_hst_acsggct"
OUT = HERE / "hst_acsggct_benchmark_results"
SIM_CACHE = base.OUT_DIR / "simulation_training_features.npz"
CLUSTERS = ("ngc6397", "ngc6752", "ngc1851")
CROP_X0 = CROP_Y0 = 2400
CROP_SIZE = 1200
PIXEL_SCALE_MAS = 50.0
MATCH_RADIUS_PX = 2.0


def read_cluster(cluster):
    prefix = f"hlsp_acsggct_hst_acs-wfc_{cluster}"
    image = fits.getdata(DATA / f"{prefix}_f606w_v2_img.fits").astype(float)
    catalog = Table.read(DATA / f"{prefix}_r.rdviq.cal.adj.zpt", format="ascii")
    crop = image[CROP_Y0:CROP_Y0+CROP_SIZE, CROP_X0:CROP_X0+CROP_SIZE].copy()
    bad = (~np.isfinite(crop)) | (crop <= -700)
    crop[bad] = np.nanmedian(crop[~bad])
    return crop, catalog


def catalog_subsets(catalog):
    x = np.asarray(catalog["x"], float) - 1.0
    y = np.asarray(catalog["y"], float) - 1.0
    inside = ((x >= CROP_X0+12) & (x < CROP_X0+CROP_SIZE-12) &
              (y >= CROP_Y0+12) & (y < CROP_Y0+CROP_SIZE-12))
    measured = inside & (np.asarray(catalog["Vvega"], float) < 90)
    quality = (measured & (np.asarray(catalog["err"], float) < 0.10) &
               (np.asarray(catalog["qfitV"], float) < 0.30) &
               (np.asarray(catalog["othv"], float) < 1.0) &
               (np.asarray(catalog["Nv"], int) >= 1))
    calibration = (quality & (np.asarray(catalog["Vvega"], float) <= 21.0) &
                   (np.asarray(catalog["err"], float) < 0.05) &
                   (np.asarray(catalog["qfitV"], float) < 0.15) &
                   (np.asarray(catalog["othv"], float) < 0.10))
    return x, y, measured, quality, calibration


def one_to_one(det_xy, ref_xy, radius=MATCH_RADIUS_PX):
    return base.greedy_match(np.asarray(det_xy, float), np.asarray(ref_xy, float), radius)


def ref_cells(ref_xy):
    local = np.asarray(ref_xy, float) - np.array([CROP_X0, CROP_Y0])
    return adapt.cell_ids(local, cell_size=200)


def fit_affine(det, ref):
    A = np.column_stack([np.ones(len(det)), det[:, 0], det[:, 1]])
    cx, *_ = np.linalg.lstsq(A, ref[:, 0], rcond=None)
    cy, *_ = np.linalg.lstsq(A, ref[:, 1], rcond=None)
    for _ in range(3):
        pred = np.column_stack([A @ cx, A @ cy])
        resid = np.sqrt(np.sum((pred-ref)**2, axis=1))
        med = np.median(resid); mad = 1.4826*np.median(np.abs(resid-med))
        good = resid <= med + max(3*mad, 0.05)
        A2 = A[good]; ref2 = ref[good]
        cx, *_ = np.linalg.lstsq(A2, ref2[:, 0], rcond=None)
        cy, *_ = np.linalg.lstsq(A2, ref2[:, 1], rcond=None)
    return cx, cy


def apply_affine(xy, coeff):
    A = np.column_stack([np.ones(len(xy)), xy[:, 0], xy[:, 1]])
    return np.column_stack([A @ coeff[0], A @ coeff[1]])


def measurement_metrics(det_xy, flux, keep, ref_xy, ref_mag, ref_partition):
    chosen = np.where(keep)[0]
    matched, ridx = one_to_one(det_xy[chosen], ref_xy)
    if np.sum(matched) < 20:
        return {"astrometric_rms_px": None, "astrometric_rms_mas": None,
                "photometric_rms_mag": None, "measurement_test_matches": int(np.sum(matched))}
    ci = chosen[matched]; ri = ridx[matched]
    train = ref_partition[ri] != 2; test = ref_partition[ri] == 2
    if np.sum(train) < 10 or np.sum(test) < 5:
        return {"astrometric_rms_px": None, "astrometric_rms_mas": None,
                "photometric_rms_mag": None, "measurement_test_matches": int(np.sum(test))}
    coeff = fit_affine(det_xy[ci][train], ref_xy[ri][train])
    pred = apply_affine(det_xy[ci][test], coeff)
    delta = pred-ref_xy[ri][test]
    radial = np.sqrt(np.sum(delta**2, axis=1))
    med = np.median(radial); mad = 1.4826*np.median(np.abs(radial-med))
    good_ast = radial <= med + max(3*mad, 0.05)
    rms_1d_px = np.sqrt(np.mean(np.sum(delta[good_ast]**2, axis=1))/2.0)
    inst = -2.5*np.log10(np.maximum(np.asarray(flux, float), 1e-6))
    zp = np.median(ref_mag[ri][train] - inst[ci][train])
    mag_resid = inst[ci][test] + zp - ref_mag[ri][test]
    mmed = np.median(mag_resid); mm = 1.4826*np.median(np.abs(mag_resid-mmed))
    good_mag = np.abs(mag_resid-mmed) <= max(3*mm, 0.03)
    phot_rms = np.sqrt(np.mean((mag_resid[good_mag]-np.mean(mag_resid[good_mag]))**2))
    return {"astrometric_rms_px": float(rms_1d_px),
            "astrometric_rms_mas": float(rms_1d_px*PIXEL_SCALE_MAS),
            "photometric_rms_mag": float(phot_rms),
            "measurement_test_matches": int(np.sum(test))}


def evaluate_mode(cluster, mode, det_xy, flux, keep, measured_xy, quality_xy,
                  quality_mag, quality_partition, measured_partition, threshold,
                  fwhm, timings, catalog_error):
    test_quality = quality_partition == 2
    test_measured = measured_partition == 2
    det_test = adapt.cell_ids(det_xy-np.array([CROP_X0,CROP_Y0]), 200) == 2
    raw_test, _ = one_to_one(det_xy[det_test], quality_xy[test_quality])
    kept_test = det_test & keep
    retained_test, _ = one_to_one(det_xy[kept_test], quality_xy[test_quality])
    all_match, _ = one_to_one(det_xy[keep], measured_xy)
    summary = {"cluster": cluster, "mode": mode, "fwhm_px": float(fwhm),
               "quality_test_references": int(np.sum(test_quality)),
               "candidates": int(len(det_xy)), "retained": int(np.sum(keep)),
               "test_raw_recall": float(np.sum(raw_test)/max(np.sum(test_quality),1)),
               "test_retained_recall": float(np.sum(retained_test)/max(np.sum(test_quality),1)),
               "catalog_match_rate_lower_bound": float(np.sum(all_match)/max(np.sum(keep),1)),
               "threshold": float(threshold), "catalog_median_reported_mag_error": float(catalog_error),
               **timings}
    for mag_limit in (18, 19, 20, 21, 22):
        subset = test_quality & (quality_mag <= mag_limit)
        match, _ = one_to_one(det_xy[kept_test], quality_xy[subset])
        summary[f"recall_v_le_{mag_limit}"] = float(np.sum(match)/max(np.sum(subset),1))
        summary[f"n_v_le_{mag_limit}"] = int(np.sum(subset))
    summary.update(measurement_metrics(det_xy, flux, keep, quality_xy, quality_mag,
                                       quality_partition))
    return summary


def run_cluster(mod, cluster, frozen_clf, frozen_threshold, X_sim, y_sim, groups_sim):
    image, cat = read_cluster(cluster)
    x,y,measured,quality,calibration = catalog_subsets(cat)
    measured_xy = np.column_stack([x[measured],y[measured]])
    quality_xy = np.column_stack([x[quality],y[quality]])
    quality_mag = np.asarray(cat["Vvega"],float)[quality]
    measured_partition = ref_cells(measured_xy); quality_partition = ref_cells(quality_xy)
    t0=time.perf_counter(); sub,rms=base.estimate_background(image); background_s=time.perf_counter()-t0
    pre=base.detect_sources(sub,rms,fwhm=2.0,threshold_sigma=10.0)
    fwhm=float(np.clip(mod.estimate_psf_fwhm(sub,pre,rms,min_snr=20,max_sources=40),1.5,4.0))
    t0=time.perf_counter(); sources=base.detect_sources(sub,rms,fwhm=fwhm,threshold_sigma=3.0); detection_s=time.perf_counter()-t0
    t0=time.perf_counter(); X,flux=mod._extract_clf_features(sources,sub,rms); features_s=time.perf_counter()-t0
    xcol,ycol=mod._xy_columns(sources)
    det_xy=np.column_stack([np.asarray(sources[xcol],float)+CROP_X0,
                           np.asarray(sources[ycol],float)+CROP_Y0])
    Xn=(X-X.mean(axis=0))/np.maximum(X.std(axis=0),1e-8)
    t0=time.perf_counter(); pz=frozen_clf.predict_proba(Xn)[:,1]; zero_inf=time.perf_counter()-t0
    matched,ref_idx=one_to_one(det_xy,measured_xy)
    # Map full-catalog calibration quality into the measured subset.
    cal_measured=np.asarray(calibration[measured],bool)
    positive=matched & (ref_idx>=0) & cal_measured[np.maximum(ref_idx,0)]
    nearest,_=cKDTree(measured_xy).query(det_xy,k=1)
    negative=(nearest>3.0)&(~positive)
    cells=adapt.cell_ids(det_xy-np.array([CROP_X0,CROP_Y0]),200)
    adapted_clf,threshold,Xtarget,meta=adapt.fit_target_adaptation(
        X_sim,y_sim,groups_sim,X,positive,negative,cells,frozen_clf,frozen_threshold)
    # HST science-catalog operating point: use the target validation partition
    # only, requiring 98% recall while retaining at least 90% catalogue coverage
    # on the conservative known-positive/known-negative calibration sample.
    known=positive|negative; val=known&(cells==1); y_known=positive.astype(int)
    if np.sum(val&positive)>=3 and np.sum(val&negative)>=3:
        pval=adapted_clf.predict_proba(Xtarget[val])[:,1]
        hst_threshold,hmeta=adapt.choose_threshold(y_known[val],pval,
                                                    target_recall=0.98,
                                                    min_precision=0.90)
        if hst_threshold is not None:
            threshold=hst_threshold
            meta.update({f"hst_validation_{k}":v for k,v in hmeta.items()})
    t0=time.perf_counter(); pa=adapted_clf.predict_proba(Xtarget)[:,1]; adapt_inf=time.perf_counter()-t0
    catalog_error=np.median(np.asarray(cat["err"],float)[quality])
    common={"background_s":background_s,"detection_s":detection_s,"features_s":features_s}
    results=[]
    results.append(evaluate_mode(cluster,"raw_proposals",det_xy,flux,np.ones(len(det_xy),bool),
                                 measured_xy,quality_xy,quality_mag,quality_partition,measured_partition,
                                 0.0,fwhm,{**common,"inference_s":0.0},catalog_error))
    results.append(evaluate_mode(cluster,"simulation_frozen",det_xy,flux,pz>=frozen_threshold,
                                 measured_xy,quality_xy,quality_mag,quality_partition,measured_partition,
                                 frozen_threshold,fwhm,{**common,"inference_s":zero_inf},catalog_error))
    adapted_result=evaluate_mode(cluster,"target_adapted",det_xy,flux,pa>=threshold,
                                 measured_xy,quality_xy,quality_mag,quality_partition,measured_partition,
                                 threshold,fwhm,{**common,"inference_s":adapt_inf},catalog_error)
    adapted_result.update(meta); results.append(adapted_result)
    output=Table()
    output["x"]=det_xy[:,0];output["y"]=det_xy[:,1];output["flux_r5"]=flux
    output["zero_shot_probability"]=pz;output["adapted_probability"]=pa
    output["known_positive"]=positive;output["known_negative"]=negative;output["spatial_partition"]=cells
    output.write(OUT/f"{cluster}_wpdc_candidates.ecsv",format="ascii.ecsv",overwrite=True)
    return results,image,det_xy,pa>=threshold,quality_xy,quality_partition


def save_figure(plot_data):
    fig,axes=plt.subplots(1,2,figsize=(12,5.4),constrained_layout=True)
    for ax,(cluster,image,xy,keep,ref,partition) in zip(axes,plot_data):
        lo,hi=np.percentile(image,[5,99.7]); ax.imshow(image,origin="lower",cmap="gray",vmin=lo,vmax=hi)
        local_ref=ref-np.array([CROP_X0,CROP_Y0]); test=partition==2
        ax.scatter(local_ref[test,0],local_ref[test,1],s=5,facecolors="none",edgecolors="#38c8ff",linewidths=.35,label="official test references")
        local_det=xy[keep]-np.array([CROP_X0,CROP_Y0])
        ax.scatter(local_det[:,0],local_det[:,1],s=3,c="#ffbd2e",alpha=.65,label="WPDC adapted")
        ax.set_title(cluster.upper()+" central 1200x1200");ax.set_xlabel("x / pixel");ax.set_ylabel("y / pixel");ax.legend(fontsize=7)
    fig.savefig(OUT/"hst_acsggct_wpdc_benchmark.png",dpi=220);plt.close(fig)


def write_report(results, sim_val):
    lines=["# WPDC on HST/ACS Galactic globular-cluster benchmarks","",
           "Datasets: public MAST HLSP ACSGGCT v2 F606W reference images and Anderson et al. catalogues for NGC 6397 and NGC 6752. Evaluation uses the central 1200x1200 pixels. Catalogue x/y coordinates are one-indexed and are shifted by -1 pixel before matching.","",
           "Quality reference: valid F606W measurement, reported error <0.10 mag, qfitV <0.30, neighbour-light fraction othv <1, and at least one F606W measurement. Spatial partitions 0/1/2 are used for target fitting/threshold calibration/final testing; test references are untouched.","",
           f"Simulation-frozen threshold={sim_val['threshold']:.4f}. Association radius=2 pixels (0.10 arcsec). Astrometric RMS is one-dimensional after a six-parameter affine fit on non-test matches; the test set alone supplies the reported residual.","",
           "| Cluster | Mode | Candidates | Retained | Test refs | Recall V<=18 | Recall V<=20 | All-quality recall | Catalogue match-rate lower bound | Astrometric RMS /mas | Photometric RMS /mag |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        ast="--" if r["astrometric_rms_mas"] is None else f"{r['astrometric_rms_mas']:.2f}"
        phot="--" if r["photometric_rms_mag"] is None else f"{r['photometric_rms_mag']:.3f}"
        lines.append(f"| {r['cluster']} | {r['mode']} | {r['candidates']} | {r['retained']} | {r['quality_test_references']} | {r['recall_v_le_18']:.3f} | {r['recall_v_le_20']:.3f} | {r['test_retained_recall']:.3f} | {r['catalog_match_rate_lower_bound']:.3f} | {ast} | {phot} |")
    lines += ["","## Literature/SOTA context","",
              "| Reference | Data/instrument | Comparable published metric | Comparison status |",
              "|---|---|---|---|",
              "| Anderson et al. (2008), AJ 135, 2055, doi:10.1088/0004-6256/135/6/2055 | Same ACSGGCT programme | Catalogue designed to be essentially 100% complete above the sub-giant branch for almost all clusters; global relative astrometry about 0.01 pixel = 0.5 mas after a general six-parameter transformation | Same dataset; catalogue is the evaluation reference, not an independent competing run |",
              "| Libralato et al. (2024), arXiv:2411.02487 | Euclid ERO NGC 6397 | VIS bright-star 1D precision 0.7 mas (0.007 pixel); external Gaia positional residual 3.2-5.3 mas | Modern crowded-field astrometric SOTA on the same cluster but a different instrument/epoch; only the mas precision is contextual |",
              "| Salaris et al. (2024), Astron. Notes 345, e20240018, doi:10.1002/asna.20240018 | NGC 6752 WFC3/IR | Artificial-star recovery requires <1 pixel and <0.75 mag; reports NIR completeness curves and 50% limits | Latest cluster paper but different filters, exposures, and truth injections; no direct numerical ranking against ACS/F606W |","",
              "The official ACS catalogue is produced with dedicated multi-exposure ePSF fitting and artificial-star tests. WPDC here operates on one public stacked image, so equality with the 0.5-0.7 mas multi-exposure SOTA is not expected. Detection recall and catalogue match rate are directly measured; literature photometric/completeness numbers with incompatible filters are not presented as a leaderboard."]
    (OUT/"hst_acsggct_sota_comparison_report.md").write_text("\n".join(lines),encoding="utf-8")


def main():
    OUT.mkdir(exist_ok=True)
    mod=adapt.load_pipeline()
    cache=np.load(SIM_CACHE,allow_pickle=False);X=cache["X"];y=cache["y"];groups=cache["groups"]
    frozen,threshold,sim_val=base.fit_frozen_classifier(X,y,groups)
    results=[];plots=[]
    for cluster in CLUSTERS:
        print(f"\nHST benchmark: {cluster}")
        rows,image,xy,keep,ref,partition=run_cluster(mod,cluster,frozen,threshold,X,y,groups)
        for row in rows: print(json.dumps(row,indent=2))
        results.extend(rows);plots.append((cluster,image,xy,keep,ref,partition))
    literature={"anderson_2008":{"doi":"10.1088/0004-6256/135/6/2055","relative_astrometry_mas":0.5,"relative_astrometry_px":0.01,"bright_completeness":"essentially 100% above SGB"},
                "libralato_2024":{"arxiv":"2411.02487","vis_bright_1d_mas":0.7,"external_gaia_rms_mas":[3.2,5.3]},
                "salaris_2024":{"doi":"10.1002/asna.20240018","directly_comparable":False,"reason":"WFC3/IR artificial-star experiment, not ACS/F606W"}}
    payload={"protocol":{"crop":[CROP_X0,CROP_Y0,CROP_SIZE,CROP_SIZE],"pixel_scale_mas":PIXEL_SCALE_MAS,"match_radius_px":MATCH_RADIUS_PX,"test_partition":2},"simulation_validation":sim_val,"literature_metrics":literature,"results":results}
    (OUT/"hst_acsggct_benchmark_summary.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    fields=sorted({k for row in results for k in row})
    with (OUT/"hst_acsggct_benchmark_summary.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(results)
    save_figure(plots);write_report(results,sim_val)
    print(OUT)


if __name__=="__main__":
    main()
