#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Simulation-to-HST target-adaptation learning curve for WPDC.

This experiment quantifies the deployment budget required after development on
CSST-like simulations.  A 1200 x 1200 HST/ACS F606W field is partitioned into
non-overlapping 200 x 200 pixel tiles.  Target labels from calibration tiles
are used only to fine-tune the candidate classifier and select its threshold;
a spatially disjoint validation set selects that threshold and a disjoint test
set is used only for reporting.  PSF estimation uses image pixels without
catalogue labels, as it would at deployment. Tiles, rather than arbitrary
source counts, are the reported calibration unit.

The public ACSGGCT images/catalogues and the project working modules are
deliberately external to this repository.  Supply their parent directory with
``--work-dir``; no restricted image data are copied into Git.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial import cKDTree
from sklearn.ensemble import RandomForestClassifier


TILE_SIZE = 200
CLUSTERS = ("ngc6397", "ngc6752", "ngc1851")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True,
                        help="External WPDC working directory containing real_data_*.py and HST data.")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Directory for the CSV, JSON, figure, and report.")
    parser.add_argument("--budgets", type=int, nargs="+", default=[0, 1, 3, 6],
                        help="Number of 200 x 200 target calibration tiles.")
    parser.add_argument("--repeats", type=int, default=5,
                        help="Independent deterministic tile selections per non-zero budget.")
    parser.add_argument("--seed", type=int, default=20260808)
    return parser.parse_args()


def tile_ids(xy):
    """Return 6x6 tile identifiers in the benchmark's 1200-pixel crop."""
    local = np.asarray(xy, float) - 2400.0
    ix = np.clip((local[:, 0] // TILE_SIZE).astype(int), 0, 5)
    iy = np.clip((local[:, 1] // TILE_SIZE).astype(int), 0, 5)
    return iy * 6 + ix


def partitions(ids):
    """Fixed spatial split: 60% train pool, 20% validation, 20% untouched test."""
    hashed = (np.asarray(ids, int) * 17 + 11) % 5
    return hashed <= 2, hashed == 3, hashed == 4


def normalize_simulation(X, groups):
    out = np.asarray(X, float).copy()
    for group in np.unique(groups):
        keep = groups == group
        out[keep] = (out[keep] - out[keep].mean(axis=0)) / np.maximum(out[keep].std(axis=0), 1e-8)
    return out


def choose_hst_threshold(adapt, y, probabilities):
    threshold, metadata = adapt.choose_threshold(y, probabilities, target_recall=0.98, min_precision=0.90)
    if threshold is None:
        return 0.9904166666666666, {"status": "frozen_due_to_insufficient_validation_labels"}
    return float(threshold), metadata


def fit_budget_model(adapt, X_sim_norm, y_sim, X_target_norm, positive, negative,
                     candidate_tiles, validation_tiles, selected_tiles, frozen, frozen_threshold, seed):
    """Fit simulation-plus-target RF without touching validation or test labels."""
    train = np.isin(candidate_tiles, selected_tiles) & (positive | negative)
    val = np.isin(candidate_tiles, validation_tiles) & (positive | negative)
    y_target = positive.astype(int)
    n_pos = int(np.sum(train & positive)); n_neg = int(np.sum(train & negative))
    n_val_pos = int(np.sum(val & positive)); n_val_neg = int(np.sum(val & negative))
    if n_pos < 5 or n_neg < 5:
        return frozen, float(frozen_threshold), {"adapted": False, "n_train_positive": n_pos,
                                                   "n_train_negative": n_neg, "n_validation_positive": n_val_pos,
                                                   "n_validation_negative": n_val_neg,
                                                   "threshold_status": "frozen_insufficient_train_labels"}
    clf = RandomForestClassifier(n_estimators=400, max_depth=15, min_samples_leaf=2,
                                 max_features="sqrt", class_weight={0: 1, 1: 6},
                                 random_state=seed, n_jobs=-1)
    Xfit = np.vstack([X_sim_norm, X_target_norm[train]])
    yfit = np.concatenate([y_sim, y_target[train]])
    weights = np.concatenate([np.ones(len(y_sim)), np.full(int(np.sum(train)), 10.0)])
    clf.fit(Xfit, yfit, sample_weight=weights)
    threshold = float(frozen_threshold)
    metadata = {"adapted": True, "n_train_positive": n_pos, "n_train_negative": n_neg,
                "n_validation_positive": n_val_pos, "n_validation_negative": n_val_neg}
    if n_val_pos >= 3 and n_val_neg >= 3:
        threshold, info = choose_hst_threshold(adapt, y_target[val], clf.predict_proba(X_target_norm[val])[:, 1])
        metadata.update({f"validation_{k}": v for k, v in info.items()})
    else:
        metadata["threshold_status"] = "frozen_insufficient_validation_labels"
    return clf, threshold, metadata


def one_to_one(base, detected, reference):
    return base.greedy_match(np.asarray(detected, float), np.asarray(reference, float), 2.0)


def target_labels(base, hst, det_xy, measured_xy, calibration_mask):
    """Create conservative target labels from bright isolated ACS catalogue stars."""
    matched, reference_index = one_to_one(base, det_xy, measured_xy)
    calib_in_measured = np.asarray(calibration_mask, bool)
    positive = matched & (reference_index >= 0)
    positive &= calib_in_measured[np.maximum(reference_index, 0)]
    nearest, _ = cKDTree(measured_xy).query(det_xy, k=1)
    negative = (nearest > 3.0) & ~positive
    return positive, negative


def evaluate(base, det_xy, probabilities, threshold, candidate_tiles, test_tiles,
             quality_xy, quality_mag, quality_tiles):
    test_det = np.isin(candidate_tiles, test_tiles)
    test_ref = np.isin(quality_tiles, test_tiles)
    keep = probabilities >= threshold
    matched, _ = one_to_one(base, det_xy[test_det & keep], quality_xy[test_ref])
    all_matched, _ = one_to_one(base, det_xy[test_det & keep], quality_xy[test_ref])
    bright = test_ref & (quality_mag <= 20.0)
    bright_matched, _ = one_to_one(base, det_xy[test_det & keep], quality_xy[bright])
    denom = max(int(np.sum(test_ref)), 1)
    retained = int(np.sum(test_det & keep))
    return {"test_references": int(np.sum(test_ref)), "test_retained": retained,
            "test_recovered": int(np.sum(matched)), "test_recall": float(np.sum(matched) / denom),
            "test_match_rate_lower_bound": float(np.sum(all_matched) / max(retained, 1)),
            "test_bright_references_v_le_20": int(np.sum(bright)),
            "test_bright_recall_v_le_20": float(np.sum(bright_matched) / max(int(np.sum(bright)), 1))}


def prepare_cluster(base, adapt, hst, module, cluster):
    image, catalogue = hst.read_cluster(cluster)
    x, y, measured, quality, calibration = hst.catalog_subsets(catalogue)
    measured_xy = np.column_stack([x[measured], y[measured]])
    quality_xy = np.column_stack([x[quality], y[quality]])
    quality_mag = np.asarray(catalogue["Vvega"], float)[quality]
    sub, rms = base.estimate_background(image)
    pre = base.detect_sources(sub, rms, fwhm=2.0, threshold_sigma=10.0)
    fwhm = float(np.clip(module.estimate_psf_fwhm(sub, pre, rms, min_snr=20, max_sources=40), 1.5, 4.0))
    sources = base.detect_sources(sub, rms, fwhm=fwhm, threshold_sigma=3.0)
    X, _ = module._extract_clf_features(sources, sub, rms)
    xcol, ycol = module._xy_columns(sources)
    det_xy = np.column_stack([np.asarray(sources[xcol], float) + 2400.0,
                              np.asarray(sources[ycol], float) + 2400.0])
    positive, negative = target_labels(base, hst, det_xy, measured_xy, calibration[measured])
    return {"cluster": cluster, "X": X, "det_xy": det_xy, "positive": positive, "negative": negative,
            "candidate_tiles": tile_ids(det_xy), "quality_xy": quality_xy, "quality_mag": quality_mag,
            "quality_tiles": tile_ids(quality_xy), "fwhm_px": fwhm}


def summarise(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault((row["cluster"], row["budget_tiles"]), []).append(row)
    summaries = []
    for (cluster, budget), group in sorted(grouped.items()):
        out = {"cluster": cluster, "budget_tiles": budget, "repeats": len(group),
               "field_fraction": budget * TILE_SIZE * TILE_SIZE / (1200 * 1200)}
        for key in ("n_train_positive", "n_train_negative", "test_recall", "test_bright_recall_v_le_20",
                    "test_match_rate_lower_bound", "test_retained", "threshold"):
            values = np.asarray([r[key] for r in group], float)
            out[f"{key}_mean"] = float(values.mean())
            out[f"{key}_ci95"] = float(1.96 * values.std(ddof=1) / np.sqrt(len(values))) if len(values) > 1 else 0.0
        summaries.append(out)
    return summaries


def plot(summaries, destination):
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 4.0), constrained_layout=True)
    for cluster in CLUSTERS:
        rows = [r for r in summaries if r["cluster"] == cluster]
        x = [r["budget_tiles"] for r in rows]
        for ax, field, label in ((axes[0], "test_recall", "All-quality recall"),
                                 (axes[1], "test_bright_recall_v_le_20", "Bright (V<=20) recall")):
            y = [r[f"{field}_mean"] for r in rows]
            e = [r[f"{field}_ci95"] for r in rows]
            ax.errorbar(x, y, yerr=e, marker="o", capsize=3, label=cluster.upper())
            ax.set_xlabel("Target calibration tiles (200 x 200 px)")
            ax.set_ylabel(label + " on untouched test tiles")
            ax.set_ylim(0, 1.03)
            ax.grid(alpha=.25)
    axes[0].legend(fontsize=8)
    fig.savefig(destination, dpi=220)
    plt.close(fig)


def write_report(summaries, destination):
    lines = ["# WPDC simulation-to-real target-adaptation budget curve", "",
             "WPDC is developed on CSST-like simulations and then lightly calibrated on spatially disjoint HST/ACS target tiles. Each tile is 200 x 200 pixels. Test-tile catalogue labels are never used for target classifier fitting or threshold selection. PSF FWHM is estimated from the full target image without catalogue labels, which is permitted in the transductive deployment setting. The target labels are conservative: bright, isolated ACS catalogue matches are positives and detections farther than 3 pixels from any measured catalogue star are negatives.", "",
             "The budget is intentionally reported as calibration tiles and label counts, not as a number of independent telescope visits. A tile is a reproducible within-field calibration unit; it must not be described as an independent image.", "",
             "| Cluster | Calibration tiles | Field fraction | Mean positive / negative labels | Test recall (95% CI) | Bright V<=20 recall (95% CI) | Test match-rate lower bound (95% CI) |", "|---|---:|---:|---:|---:|---:|---:|"]
    for r in summaries:
        lines.append(f"| {r['cluster']} | {r['budget_tiles']} | {r['field_fraction']:.3f} | {r['n_train_positive_mean']:.1f} / {r['n_train_negative_mean']:.1f} | {r['test_recall_mean']:.3f} +/- {r['test_recall_ci95']:.3f} | {r['test_bright_recall_v_le_20_mean']:.3f} +/- {r['test_bright_recall_v_le_20_ci95']:.3f} | {r['test_match_rate_lower_bound_mean']:.3f} +/- {r['test_match_rate_lower_bound_ci95']:.3f} |")
    lines += ["", "Interpretation: a practical deployment can first estimate the PSF from the target image without labels, then use a small labelled calibration region to adapt the candidate distribution and select a conservative operating threshold. The conclusions concern target-domain candidate recovery and catalogue coverage only; they do not claim a full external photometric calibration or universal SOTA."]
    destination.write_text("\n".join(lines), encoding="utf-8")


def main():
    args = parse_args()
    if any(b < 0 for b in args.budgets):
        raise ValueError("Budgets must be non-negative.")
    sys.path.insert(0, str(args.work_dir.resolve()))
    import real_data_zero_shot_generalization as base
    import real_data_domain_adaptation as adapt
    import hst_acsggct_benchmark as hst

    args.output_dir.mkdir(parents=True, exist_ok=True)
    module = adapt.load_pipeline()
    cache = np.load(base.OUT_DIR / "simulation_training_features.npz", allow_pickle=False)
    X_sim, y_sim, groups = cache["X"], cache["y"], cache["groups"]
    frozen, frozen_threshold, simulation_validation = base.fit_frozen_classifier(X_sim, y_sim, groups)
    X_sim_norm = normalize_simulation(X_sim, groups)
    rng = np.random.default_rng(args.seed)
    rows = []
    started = time.perf_counter()
    for cluster in CLUSTERS:
        print(f"Preparing {cluster}", flush=True)
        data = prepare_cluster(base, adapt, hst, module, cluster)
        X_target_norm = (data["X"] - data["X"].mean(axis=0)) / np.maximum(data["X"].std(axis=0), 1e-8)
        pool, validation, test = partitions(data["candidate_tiles"])
        _, _, reference_test = partitions(data["quality_tiles"])
        pool_tiles = np.unique(data["candidate_tiles"][pool])
        validation_tiles = np.unique(data["candidate_tiles"][validation])
        test_tiles = np.unique(data["candidate_tiles"][test])
        for budget in sorted(set(args.budgets)):
            repetitions = 1 if budget == 0 else args.repeats
            if budget > len(pool_tiles):
                raise ValueError(f"{cluster}: requested {budget} tiles, only {len(pool_tiles)} in training pool")
            for repeat in range(repetitions):
                selected = np.array([], dtype=int) if budget == 0 else rng.choice(pool_tiles, size=budget, replace=False)
                classifier, threshold, metadata = fit_budget_model(
                    adapt, X_sim_norm, y_sim, X_target_norm, data["positive"], data["negative"],
                    data["candidate_tiles"], validation_tiles, selected, frozen, frozen_threshold,
                    args.seed + 1000 * (CLUSTERS.index(cluster) + 1) + 100 * budget + repeat)
                probabilities = classifier.predict_proba(X_target_norm)[:, 1]
                metrics = evaluate(base, data["det_xy"], probabilities, threshold, data["candidate_tiles"],
                                   test_tiles, data["quality_xy"], data["quality_mag"], data["quality_tiles"])
                rows.append({"cluster": cluster, "budget_tiles": budget, "repeat": repeat,
                             "selected_tiles": ";".join(map(str, selected.tolist())), "fwhm_px": data["fwhm_px"],
                             "threshold": threshold, **metadata, **metrics})
                print(f"{cluster}: budget={budget}, repeat={repeat}, recall={metrics['test_recall']:.3f}", flush=True)
    summaries = summarise(rows)
    with (args.output_dir / "adaptation_budget_raw.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted({key for row in rows for key in row}))
        writer.writeheader(); writer.writerows(rows)
    with (args.output_dir / "adaptation_budget_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]))
        writer.writeheader(); writer.writerows(summaries)
    payload = {"protocol": {"tile_size_px": TILE_SIZE, "budgets": sorted(set(args.budgets)), "repeats": args.repeats,
                            "spatial_split": "60% target-fit pool / 20% threshold-validation / 20% untouched test"},
               "simulation_validation": simulation_validation, "wall_time_s": time.perf_counter() - started,
               "summaries": summaries}
    (args.output_dir / "adaptation_budget_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    plot(summaries, args.output_dir / "adaptation_budget_curve.png")
    write_report(summaries, args.output_dir / "adaptation_budget_report.md")
    print(args.output_dir)


if __name__ == "__main__":
    main()
