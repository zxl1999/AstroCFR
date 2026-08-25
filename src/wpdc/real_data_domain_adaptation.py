#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Few-shot target-domain adaptation for the WPDC real-data stress test.

The zero-shot experiment remains the reference baseline.  The adapted branch
adds only information that would be available in a small deployment
calibration visit: an unlabeled image PSF estimate and spatially held-out Gaia
labels for bright, isolated stars.  The held-out spatial cells are never used
for fitting or threshold selection.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from astropy.table import Table
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from scipy.spatial import cKDTree
from sklearn.ensemble import RandomForestClassifier

import real_data_zero_shot_generalization as base


OUT = base.OUT_DIR
SIM_CACHE = OUT / "simulation_training_features.npz"
RNG = np.random.default_rng(20260806)


def load_pipeline():
    spec = importlib.util.spec_from_file_location("wpdc_domain_adapt_pipeline", base.PIPELINE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def normalize_by_group(X, groups):
    Xn = np.asarray(X, float).copy()
    for group in np.unique(groups):
        mask = groups == group
        Xn[mask] = (Xn[mask] - Xn[mask].mean(axis=0)) / np.maximum(Xn[mask].std(axis=0), 1e-8)
    return Xn


def estimate_psf_and_detect(mod, sub, rms):
    """Estimate FWHM from bright image-only candidates, then detect adaptively."""
    pre = base.detect_sources(sub, rms, fwhm=3.0, threshold_sigma=8.0)
    fwhm = 3.5
    if len(pre) >= 12:
        try:
            estimate = float(mod.estimate_psf_fwhm(sub, pre, rms, min_snr=20.0, max_sources=40))
            if np.isfinite(estimate):
                fwhm = float(np.clip(estimate, 1.5, 8.0))
        except Exception as exc:
            print(f"  PSF estimate fallback: {exc}")
    # A lower threshold is a detector adaptation, not a label-derived choice.
    sources = base.detect_sources(sub, rms, fwhm=fwhm, threshold_sigma=3.0)
    return sources, fwhm, len(pre)


def cell_ids(xy, cell_size=200):
    xy = np.asarray(xy, float)
    return (np.floor(xy[:, 0] / cell_size).astype(int) +
            np.floor(xy[:, 1] / cell_size).astype(int)) % 3


def label_calibration_candidates(xy, gaia, radius_px, fwhm):
    """Return known positive/negative masks and one-to-one raw matches.

    Bright isolated Gaia references are positive labels.  Candidates farther
    than both 8 pixels and 2x the association radius from every Gaia row are
    conservative negatives.  The remainder is explicitly unknown and ignored.
    """
    ref_xy = np.column_stack([np.asarray(gaia["x"], float), np.asarray(gaia["y"], float)])
    matched, ref_idx = base.greedy_match(xy, ref_xy, radius_px)
    ref_tree = cKDTree(ref_xy) if len(ref_xy) else None
    if ref_tree is None:
        return (np.zeros(len(xy), bool), np.zeros(len(xy), bool), matched,
                ref_idx, np.zeros(len(gaia), bool), np.full(len(xy), np.inf))
    nearest, _ = ref_tree.query(ref_xy, k=2)
    isolated_ref = nearest[:, 1] >= max(2.0 * fwhm, 2.0 * radius_px)
    isolated_ref &= np.asarray(gaia["gmag"], float) <= 19.5
    nearest_det, _ = ref_tree.query(xy, k=1)
    positive = matched & (ref_idx >= 0)
    positive &= isolated_ref[np.maximum(ref_idx, 0)]
    negative = nearest_det > max(8.0, 2.0 * radius_px)
    negative &= ~positive
    return positive, negative, matched, ref_idx, isolated_ref, nearest_det


def choose_threshold(y, proba, target_recall=0.90, min_precision=0.10):
    if len(y) == 0 or np.sum(y == 1) < 3:
        return None, {"status": "insufficient_positive_validation_labels"}
    candidates = np.unique(np.r_[proba, 0.0])
    feasible = []
    for threshold in candidates:
        pred = proba >= threshold
        tp = int(np.sum(pred & (y == 1)))
        recall = tp / max(int(np.sum(y == 1)), 1)
        precision = tp / max(int(np.sum(pred)), 1)
        if recall >= target_recall and precision >= min_precision:
            feasible.append((precision, threshold, recall))
    if feasible:
        precision, threshold, recall = max(feasible, key=lambda z: (z[1], z[0]))
        status = "target_recall_with_min_precision"
    else:
        f1s = []
        for threshold in candidates:
            pred = proba >= threshold
            tp = int(np.sum(pred & (y == 1))); fp = int(np.sum(pred & (y == 0)))
            fn = int(np.sum((~pred) & (y == 1)))
            precision = tp / max(tp + fp, 1); recall = tp / max(tp + fn, 1)
            f1s.append((2*precision*recall/max(precision+recall, 1e-12), threshold, precision, recall))
        _, threshold, precision, recall = max(f1s, key=lambda z: z[0])
        status = "best_f1_fallback"
    return float(threshold), {"status": status, "recall": float(recall), "precision": float(precision), "n": int(len(y))}


def fit_target_adaptation(X_sim, y_sim, groups_sim, X_target, positive, negative,
                          cells, frozen_clf, frozen_threshold):
    X_sim_n = normalize_by_group(X_sim, groups_sim)
    target_mean = X_target.mean(axis=0); target_std = np.maximum(X_target.std(axis=0), 1e-8)
    X_target_n = (X_target - target_mean) / target_std
    known = positive | negative
    train_mask = known & (cells == 0)
    val_mask = known & (cells == 1)
    y_target = positive.astype(int)
    n_train_pos = int(np.sum(train_mask & positive)); n_train_neg = int(np.sum(train_mask & negative))
    n_val_pos = int(np.sum(val_mask & positive)); n_val_neg = int(np.sum(val_mask & negative))
    meta = {"n_calibration_train_pos": n_train_pos, "n_calibration_train_neg": n_train_neg,
            "n_calibration_val_pos": n_val_pos, "n_calibration_val_neg": n_val_neg,
            "target_finetune": False}
    adapted = frozen_clf
    threshold = frozen_threshold
    if n_train_pos >= 5 and n_train_neg >= 5:
        Xfit = np.vstack([X_sim_n, X_target_n[train_mask]])
        yfit = np.concatenate([y_sim, y_target[train_mask]])
        weights = np.concatenate([np.ones(len(y_sim)), np.full(np.sum(train_mask), 10.0)])
        adapted = RandomForestClassifier(n_estimators=400, max_depth=15, min_samples_leaf=2,
                                         max_features="sqrt", class_weight={0: 1, 1: 6},
                                         random_state=20260806, n_jobs=-1)
        adapted.fit(Xfit, yfit, sample_weight=weights)
        meta["target_finetune"] = True
        if n_val_pos >= 3 and n_val_neg >= 3:
            val_proba = adapted.predict_proba(X_target_n[val_mask])[:, 1]
            threshold, tmeta = choose_threshold(y_target[val_mask], val_proba)
            if threshold is not None:
                meta.update({f"validation_{k}": v for k, v in tmeta.items()})
            else:
                threshold = frozen_threshold
        else:
            meta["threshold_status"] = "frozen_due_to_insufficient_validation_labels"
    else:
        meta["threshold_status"] = "frozen_due_to_insufficient_calibration_labels"
    return adapted, float(threshold), X_target_n, meta


def evaluate_predictions(xy, proba, keep, gaia, cells, mode, base_info, radius_px):
    ref_xy = np.column_stack([np.asarray(gaia["x"], float), np.asarray(gaia["y"], float)])
    raw_match, _ = base.greedy_match(xy, ref_xy, radius_px)
    filt_match, _ = base.greedy_match(xy[keep], ref_xy, radius_px)
    test_ref_mask = cell_ids(ref_xy) == 2
    test_det_mask = cells == 2
    raw_test, _ = base.greedy_match(xy[test_det_mask], ref_xy[test_ref_mask], radius_px)
    filt_test, _ = base.greedy_match(xy[test_det_mask & keep], ref_xy[test_ref_mask], radius_px)
    nref = len(gaia); ntest = int(np.sum(test_ref_mask))
    return {"mode": mode, "domain": base_info["name"], "survey": base_info["survey"], "band": base_info["band"],
            "gaia_reference_g_le_20": int(nref), "test_gaia_references": ntest,
            "candidates": int(len(xy)), "retained": int(np.sum(keep)),
            "raw_matched_gaia": int(raw_match.sum()), "retained_matched_gaia": int(filt_match.sum()),
            "raw_gaia_recall": float(raw_match.sum()/max(nref,1)), "retained_gaia_recall": float(filt_match.sum()/max(nref,1)),
            "test_raw_gaia_recall": float(raw_test.sum()/max(ntest,1)), "test_retained_gaia_recall": float(filt_test.sum()/max(ntest,1)),
            "raw_gaia_match_rate": float(raw_match.sum()/max(len(xy),1)),
            "retained_gaia_match_rate": float(filt_match.sum()/max(int(np.sum(keep)),1)),
            "test_retained_gaia_match_rate": float(filt_test.sum()/max(int(np.sum(test_det_mask & keep)),1)),
            "threshold": float(base_info.get("threshold", np.nan)), "pixel_scale_arcsec": base_info["pixel_scale_arcsec"],
            "psf_fwhm_px": base_info.get("psf_fwhm_px", np.nan), "background_rms": base_info["background_rms"],
            **{k:v for k,v in base_info.items() if k.endswith("_s")}}


def run_domain(mod, domain, frozen_clf, frozen_threshold, X_sim, y_sim, groups_sim):
    image_path = base.download_ps1(domain) if domain["survey"] == "Pan-STARRS1" else base.download_legacy(domain)
    gaia_path = base.download_gaia(domain, image_path)
    image, header = base.read_image(image_path)
    gaia = base.read_gaia(gaia_path, header, g_limit=20.0)
    scale = float(np.mean(proj_plane_pixel_scales(WCS(header))) * 3600.0)
    radius_px = 0.75 / scale
    # Baseline: the exact fixed settings used in the previous zero-shot run.
    t0=time.perf_counter(); sub, rms=base.estimate_background(image); bkg_s=time.perf_counter()-t0
    t0=time.perf_counter(); baseline_sources=base.detect_sources(sub,rms,fwhm=3.5,threshold_sigma=4.0); base_det_s=time.perf_counter()-t0
    t0=time.perf_counter(); Xb, xyb=base.source_features(mod,baseline_sources,sub,rms); base_feat_s=time.perf_counter()-t0
    Xbn=(Xb-Xb.mean(axis=0))/np.maximum(Xb.std(axis=0),1e-8)
    t0=time.perf_counter(); pb=frozen_clf.predict_proba(Xbn)[:,1]; base_inf_s=time.perf_counter()-t0
    base_cells=cell_ids(xyb)
    base_meta={"name":domain["name"],"survey":domain["survey"],"band":domain["band"],"pixel_scale_arcsec":scale,
               "background_rms":float(rms),"psf_fwhm_px":3.5,"threshold":frozen_threshold,
               "background_s":bkg_s,"detection_s":base_det_s,"features_s":base_feat_s,"inference_s":base_inf_s}
    base_sum=evaluate_predictions(xyb,pb,pb>=frozen_threshold,gaia,base_cells,"zero_shot",base_meta,radius_px)
    base_sum.update({"n_calibration_train_pos":0,"n_calibration_train_neg":0,"n_calibration_val_pos":0,"n_calibration_val_neg":0})
    # Adapted detector: estimate FWHM from image-only bright candidates and lower
    # the proposal threshold; neither operation reads Gaia.
    t0=time.perf_counter(); adapted_sources, fwhm, npre=estimate_psf_and_detect(mod,sub,rms); adapt_det_s=time.perf_counter()-t0
    if len(adapted_sources)==0: raise RuntimeError(f"No adapted candidates in {domain['name']}")
    t0=time.perf_counter(); Xa,xya=base.source_features(mod,adapted_sources,sub,rms); adapt_feat_s=time.perf_counter()-t0
    Xan=(Xa-Xa.mean(axis=0))/np.maximum(Xa.std(axis=0),1e-8)
    positive,negative,raw_match,ref_idx,isolated,nearest=label_calibration_candidates(xya,gaia,radius_px,fwhm)
    cells=cell_ids(xya)
    adapted_clf, adapted_threshold, Xtarget_n, adapt_meta=fit_target_adaptation(
        X_sim,y_sim,groups_sim,Xa,positive,negative,cells,frozen_clf,frozen_threshold)
    t0=time.perf_counter(); pa=adapted_clf.predict_proba(Xtarget_n)[:,1]; adapt_inf_s=time.perf_counter()-t0
    adapt_info={"name":domain["name"],"survey":domain["survey"],"band":domain["band"],"pixel_scale_arcsec":scale,
                "background_rms":float(rms),"psf_fwhm_px":float(fwhm),"threshold":adapted_threshold,
                "background_s":bkg_s,"detection_s":adapt_det_s,"features_s":adapt_feat_s,"inference_s":adapt_inf_s}
    adapt_sum=evaluate_predictions(xya,pa,pa>=adapted_threshold,gaia,cells,"domain_adapted",adapt_info,radius_px)
    adapt_sum.update(adapt_meta); adapt_sum["bright_psf_candidates"]=int(npre)
    # Candidate-level audit products retain the calibration partition and label
    # status so every reported number can be reconstructed independently.
    for mode,xy,proba,keep,cell,pos,neg in (
            ("zero_shot",xyb,pb,pb>=frozen_threshold,base_cells,np.zeros(len(xyb),bool),np.zeros(len(xyb),bool)),
            ("domain_adapted",xya,pa,pa>=adapted_threshold,cells,positive,negative)):
        cat=Table()
        cat["x"]=xy[:,0]; cat["y"]=xy[:,1]; cat["probability"]=proba; cat["retained"]=keep
        cat["spatial_partition"]=cell; cat["known_positive"]=pos; cat["known_negative"]=neg
        cat.write(OUT/f"{domain['name']}_{mode}_candidates.ecsv",format="ascii.ecsv",overwrite=True)
    gaia_out=gaia.copy(); gaia_out["spatial_partition"]=cell_ids(np.column_stack([gaia["x"],gaia["y"]]))
    gaia_out.write(OUT/f"{domain['name']}_gaia_spatial_split.ecsv",format="ascii.ecsv",overwrite=True)
    return [base_sum,adapt_sum], [(base_sum,image,xyb,pb>=frozen_threshold,gaia),
                                  (adapt_sum,image,xya,pa>=adapted_threshold,gaia)]


def save_figure(plot_data):
    fig,axes=plt.subplots(len(base.DOMAINS),2,figsize=(12,10),constrained_layout=True)
    axes=np.atleast_2d(axes)
    for ax,(summary,image,xy,keep,gaia) in zip(axes.ravel(),plot_data):
        lo,hi=np.nanpercentile(image,[5,99.5]); ax.imshow(image,origin="lower",cmap="gray",vmin=lo,vmax=hi)
        ax.scatter(gaia["x"],gaia["y"],s=8,facecolors="none",edgecolors="#35c6ff",linewidths=.45,label="Gaia DR3 G<=20")
        ax.scatter(xy[keep,0],xy[keep,1],s=5,c="#ffbf2f",alpha=.7,label="retained")
        ax.set_title(f"{summary['domain']} | {summary['mode']}\nheld-out recall={summary['test_retained_gaia_recall']:.3f}")
        ax.set_xlabel("x (pixel)"); ax.set_ylabel("y (pixel)"); ax.legend(fontsize=7,loc="upper right")
    fig.savefig(OUT/"domain_adaptation_comparison.png",dpi=220); plt.close(fig)


def write_report(sim_val, results):
    by={(r["domain"],r["mode"]):r for r in results}
    lines=["# WPDC real-domain adaptation comparison","",
           "The zero-shot branch exactly retains the previous simulation-only model and fixed real-image detector. The adapted branch estimates PSF FWHM from bright image-only candidates, lowers the proposal threshold from 4 sigma to 3 sigma, adds spatially separated bright-isolated Gaia positives and conservative distant negatives to the simulation training set with 10x target weight, and selects its operating threshold only on a separate calibration partition.","",
           "Spatial protocol: 200-pixel checkerboard cells are assigned modulo 3 to target fine-tuning (partition 0), threshold calibration (partition 1), and final testing (partition 2). Partition-2 labels are never used for fitting or threshold selection. Gaia match rate remains a catalogue-coverage lower bound rather than true purity.","",
           f"Simulation validation retained for the zero-shot baseline: threshold={sim_val['threshold']:.4f}, recall={sim_val['recall']:.3f}, precision={sim_val['precision']:.3f}.","",
           "| Domain | Mode | PSF FWHM (px) | Candidates | Retained | Held-out Gaia refs | Held-out raw recall | Held-out retained recall | Retained Gaia match-rate lower bound | Threshold |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        lines.append(f"| {r['domain']} | {r['mode']} | {r['psf_fwhm_px']:.2f} | {r['candidates']} | {r['retained']} | {r['test_gaia_references']} | {r['test_raw_gaia_recall']:.3f} | {r['test_retained_gaia_recall']:.3f} | {r['retained_gaia_match_rate']:.3f} | {r['threshold']:.4f} |")
    lines += ["","## Change on the untouched spatial test partition",""]
    for domain in [d["name"] for d in base.DOMAINS]:
        z=by[(domain,"zero_shot")]; a=by[(domain,"domain_adapted")]
        lines.append(f"- {domain}: retained Gaia recall {z['test_retained_gaia_recall']:.3f} -> {a['test_retained_gaia_recall']:.3f}; raw proposal recall {z['test_raw_gaia_recall']:.3f} -> {a['test_raw_gaia_recall']:.3f}; retained Gaia match-rate lower bound {z['retained_gaia_match_rate']:.3f} -> {a['retained_gaia_match_rate']:.3f}.")
    lines += ["","## Calibration sample audit","",
              "| Domain | Train positives | Train negatives | Validation positives | Validation negatives | Target fine-tune |",
              "|---|---:|---:|---:|---:|---|"]
    for domain in [d["name"] for d in base.DOMAINS]:
        a=by[(domain,"domain_adapted")]
        lines.append(f"| {domain} | {a['n_calibration_train_pos']} | {a['n_calibration_train_neg']} | {a['n_calibration_val_pos']} | {a['n_calibration_val_neg']} | {a.get('target_finetune',False)} |")
    lines += ["","## Runtime","",
              "| Domain | Mode | Background (s) | Detection/PSF (s) | Features (s) | Inference (s) |",
              "|---|---|---:|---:|---:|---:|"]
    for r in results:
        lines.append(f"| {r['domain']} | {r['mode']} | {r['background_s']:.3f} | {r['detection_s']:.3f} | {r['features_s']:.3f} | {r['inference_s']:.3f} |")
    lines += ["","Interpretation: this experiment tests whether small, explicitly disclosed target calibration improves transfer. It is not zero-shot generalization and must be described as supervised few-shot domain adaptation. Full WPDC astrometric/photometric calibration remains excluded because the available Gaia rows are not an equivalent complete photometric truth catalogue."]
    (OUT/"real_data_domain_adaptation_report.md").write_text("\n".join(lines),encoding="utf-8")


def main():
    OUT.mkdir(exist_ok=True)
    mod=load_pipeline()
    cached=np.load(SIM_CACHE,allow_pickle=False)
    X_sim=cached["X"]; y_sim=cached["y"]; groups_sim=cached["groups"]
    frozen_clf,frozen_threshold,sim_val=base.fit_frozen_classifier(X_sim,y_sim,groups_sim)
    results=[]; plot_data=[]
    for domain in base.DOMAINS:
        print(f"\nDomain-adaptation comparison: {domain['name']}")
        summaries,plots=run_domain(mod,domain,frozen_clf,frozen_threshold,X_sim,y_sim,groups_sim)
        for summary in summaries: print(json.dumps(summary,indent=2))
        results.extend(summaries); plot_data.extend(plots)
    payload={"simulation_validation":sim_val,"protocol":{"cell_size_px":200,"train_partition":0,"threshold_partition":1,"test_partition":2,"gaia_positive_g_limit":19.5,"match_radius_arcsec":0.75,"target_sample_weight":10.0},"results":results}
    (OUT/"real_data_domain_adaptation_summary.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    fields=sorted({key for row in results for key in row})
    with (OUT/"real_data_domain_adaptation_summary.csv").open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader(); writer.writerows(results)
    save_figure(plot_data); write_report(sim_val,results)
    print(f"\nCompleted domain adaptation results: {OUT}")


if __name__ == "__main__":
    main()
