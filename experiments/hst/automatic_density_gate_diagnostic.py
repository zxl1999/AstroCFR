#!/usr/bin/env python
"""Audit an image-only density proxy before claiming automatic routing.

This deliberately *does not* use a catalogue at inference.  A 3-sigma
image-only proposal list supplies the local candidate count and robust local
background RMS at a query location.  The official ACSGGCT catalogue is used
only to (i) define the registered low (0--1) and high (>=3) density classes
and (ii) choose a gate threshold in the left-hand calibration strip.  The
right-hand strip is held out completely from threshold selection.

The diagnostic is intentionally separate from the main router result.  It
tests whether a catalogue-free gate is feasible; it does not yet constitute
an end-to-end mixed-branch runtime or recovery claim.

The public HST data are not bundled in the repository.  Supply the existing
upstream experiment directory with --upstream, or set ASTROCFR_UPSTREAM.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial import cKDTree
from sklearn.ensemble import RandomForestClassifier


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "results" / "hst_automatic_density_gate"


def upstream_path(value: str | None) -> Path:
    candidate = value or os.environ.get("ASTROCFR_UPSTREAM")
    if not candidate:
        raise SystemExit("Provide --upstream PATH or set ASTROCFR_UPSTREAM.")
    path = Path(candidate).expanduser().resolve()
    if not (path / "hst_acsggct_benchmark.py").exists():
        raise SystemExit(f"No upstream HST scripts found in {path}")
    return path


def best_threshold(values: np.ndarray, labels: np.ndarray) -> tuple[float, dict]:
    """Choose the high-density threshold only from calibration samples."""
    choices = np.unique(np.r_[values, values + 0.5])
    best: tuple[float, float, dict] | None = None
    for threshold in choices:
        pred = values >= threshold
        tp = int(np.sum(pred & labels)); tn = int(np.sum(~pred & ~labels))
        fp = int(np.sum(pred & ~labels)); fn = int(np.sum(~pred & labels))
        tpr = tp / max(tp + fn, 1); tnr = tn / max(tn + fp, 1)
        payload = {"tp": tp, "tn": tn, "fp": fp, "fn": fn,
                   "balanced_accuracy": (tpr + tnr) / 2,
                   "sensitivity": tpr, "specificity": tnr}
        key = (payload["balanced_accuracy"], -float(threshold))
        if best is None or key > best[:2]:
            best = (key[0], key[1], {"threshold": float(threshold), **payload})
    assert best is not None
    return best[2]["threshold"], best[2]


def score(values: np.ndarray, labels: np.ndarray, threshold: float) -> dict:
    pred = values >= threshold
    tp = int(np.sum(pred & labels)); tn = int(np.sum(~pred & ~labels))
    fp = int(np.sum(pred & ~labels)); fn = int(np.sum(~pred & labels))
    return {"n": int(len(labels)), "tp": tp, "tn": tn, "fp": fp, "fn": fn,
            "accuracy": float((tp + tn) / max(len(labels), 1)),
            "sensitivity": float(tp / max(tp + fn, 1)),
            "specificity": float(tn / max(tn + fp, 1)),
            "balanced_accuracy": float((tp / max(tp + fn, 1) + tn / max(tn + fp, 1)) / 2),
            "predicted_high_fraction": float(np.mean(pred))}


def high_recall_threshold(scores: np.ndarray, labels: np.ndarray, target: float = .90) -> tuple[float, dict]:
    """Select an operating point from calibration data, preferring recovery.

    The router's expensive branch is intended to protect crowded locations,
    so threshold selection fixes a high-density sensitivity target first and
    then maximizes specificity.  This rule is set before looking at the held
    out strip.
    """
    candidates = np.unique(np.r_[0.0, scores, 1.0])
    eligible: list[tuple[float, dict]] = []
    for threshold in candidates:
        metrics = score(scores, labels, float(threshold))
        if metrics["sensitivity"] >= target:
            eligible.append((float(threshold), metrics))
    if not eligible:
        threshold = 0.0
        return threshold, score(scores, labels, threshold)
    return max(eligible, key=lambda item: (item[1]["specificity"], item[0]))


def image_features(image: np.ndarray, candidate_tree: cKDTree, points: np.ndarray) -> np.ndarray:
    """Extract fixed image-only local crowding features at query positions."""
    rows: list[list[float]] = []
    h, w = image.shape
    for x, y in points:
        ix, iy = int(round(x)), int(round(y))
        patch = image[max(0, iy - 12):min(h, iy + 13), max(0, ix - 12):min(w, ix + 13)]
        pixels = patch[np.isfinite(patch)]
        median = float(np.median(pixels))
        rows.append([float(len(candidate_tree.query_ball_point((x, y), radius))) for radius in (8, 10, 15, 20)] +
                    [float(np.std(pixels)), float(np.percentile(pixels, 90) - median),
                     float(np.max(pixels) - median)])
    return np.asarray(rows, dtype=float)


def artificial_protocol_points(ref: np.ndarray, *, rng: np.random.Generator,
                               x_min: float, x_max: float, size: int,
                               batches: int = 5, per_batch: int = 20) -> list[dict]:
    """Regenerate density-stratified query positions without injecting flux.

    These positions reproduce the expanded-artificial-star density rule but
    are used only to assess a gate feature.  They are kept in the spatially
    held-out right strip and never participate in threshold selection.
    """
    tree = cKDTree(ref)
    records: list[dict] = []
    for band, (low, high) in (("low", (0, 1)), ("high", (3, 100000))):
        for batch in range(batches):
            points: list[tuple[float, float]] = []
            tries = 0
            while len(points) < per_batch and tries < 200000:
                tries += 1
                x, y = rng.uniform(x_min + 15, x_max - 15), rng.uniform(15, size - 15)
                density = len(tree.query_ball_point((x, y), 10.0))
                if low <= density <= high and all(np.hypot(x-a, y-b) > 12 for a, b in points):
                    points.append((x, y))
            if len(points) != per_batch:
                raise RuntimeError(f"Only generated {len(points)}/{per_batch} {band} points in batch {batch}")
            records.extend({"band": band, "batch": batch, "x": x, "y": y,
                            "reference_neighbours_10px": len(tree.query_ball_point((x, y), 10.0))}
                           for x, y in points)
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", help="directory containing the original HST experiment scripts")
    parser.add_argument("--cluster", default="ngc6752", choices=("ngc6397", "ngc6752", "ngc1851"))
    args = parser.parse_args()
    upstream = upstream_path(args.upstream)
    sys.path.insert(0, str(upstream))
    import hst_acsggct_benchmark as hst  # upstream data settings are intentional
    import real_data_zero_shot_generalization as base

    image, catalogue = hst.read_cluster(args.cluster)
    subtracted, global_rms = base.estimate_background(image)
    candidates = base.detect_sources(subtracted, global_rms, fwhm=2.2, threshold_sigma=3.0)
    candidate_xy = np.c_[np.asarray(candidates["xcentroid"], float), np.asarray(candidates["ycentroid"], float)]
    candidate_tree = cKDTree(candidate_xy)

    x, y, _, quality, _ = hst.catalog_subsets(catalogue)
    reference_xy = np.c_[x[quality] - hst.CROP_X0, y[quality] - hst.CROP_Y0]
    reference_tree = cKDTree(reference_xy)
    reference_density = np.array([len(reference_tree.query_ball_point(p, 10.0)) - 1 for p in reference_xy])
    keep = (reference_density <= 1) | (reference_density >= 3)
    label_high = reference_density[keep] >= 3
    proxy = np.array([len(candidate_tree.query_ball_point(p, 10.0)) for p in reference_xy[keep]])
    features = image_features(subtracted, candidate_tree, reference_xy[keep])

    # Strict geometry: left third selects the operating point, right third audits it.
    ref_x = reference_xy[keep, 0]
    calibration = ref_x < hst.CROP_SIZE / 3
    held_out = ref_x >= 2 * hst.CROP_SIZE / 3
    threshold, calibration_metrics = best_threshold(proxy[calibration], label_high[calibration])
    held_metrics = score(proxy[held_out], label_high[held_out], threshold)

    # A shallow, balanced RF is a calibrated density estimator, not a new
    # source classifier.  All inputs are image-derived and the only labels are
    # in the left calibration strip.  The gate threshold favours high-density
    # sensitivity because false low-cost routing is the harmful failure mode.
    gate = RandomForestClassifier(n_estimators=300, max_depth=5, min_samples_leaf=10,
                                  class_weight="balanced", n_jobs=-1, random_state=20260810)
    gate.fit(features[calibration], label_high[calibration])
    calibration_scores = gate.predict_proba(features[calibration])[:, 1]
    gate_threshold, gate_calibration = high_recall_threshold(calibration_scores, label_high[calibration], .90)
    held_scores = gate.predict_proba(features[held_out])[:, 1]
    gate_held = score(held_scores, label_high[held_out], gate_threshold)

    points = artificial_protocol_points(reference_xy, rng=np.random.default_rng(20260810),
                                        x_min=2 * hst.CROP_SIZE / 3, x_max=hst.CROP_SIZE,
                                        size=hst.CROP_SIZE)
    point_xy = np.array([(r["x"], r["y"]) for r in points])
    point_proxy = np.array([len(candidate_tree.query_ball_point(p, 10.0)) for p in point_xy])
    point_labels = np.array([r["band"] == "high" for r in points])
    artificial_metrics = score(point_proxy, point_labels, threshold)
    point_features = image_features(subtracted, candidate_tree, point_xy)
    point_scores = gate.predict_proba(point_features)[:, 1]
    gate_artificial = score(point_scores, point_labels, gate_threshold)
    for rec, value, score_value, pred in zip(points, point_proxy, point_scores, point_scores >= gate_threshold):
        rec["candidate_neighbours_10px"] = int(value)
        rec["lightweight_gate_score"] = float(score_value)
        rec["route_high_cost_branch"] = bool(pred)

    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "protocol": {
            "cluster": args.cluster,
            "input_image": "public ACSGGCT F606W central 1200x1200 crop via supplied upstream path",
            "single_feature_proxy": "number of image-only 3-sigma candidates within 10 pixels",
            "lightweight_gate": "balanced RandomForest (300 trees, depth 5) on 4 candidate-density radii plus 3 local image-texture features",
            "inference_uses_catalogue": False,
            "calibration_geometry": "x < 400 pixels; official catalogue used only to select threshold",
            "test_geometry": "x >= 800 pixels; never used in threshold selection",
            "classes": "low: 0--1, high: >=3 official quality references within 10 pixels",
            "artificial_query_points": "five batches of 20 per low/high stratum; no flux injected because this is a gate-feature audit",
            "limitation": "This diagnostic validates gate separability only. It does not report mixed-branch recovery, purity, or runtime.",
        },
        "candidate_count": int(len(candidate_xy)),
        "global_background_rms": float(global_rms),
        "single_count_baseline": {
            "threshold_candidate_neighbours_10px": float(threshold),
            "calibration": {"n": int(np.sum(calibration)), **calibration_metrics},
            "held_out_catalogue_references": held_metrics,
            "held_out_artificial_protocol_positions": artificial_metrics,
        },
        "lightweight_image_only_gate": {
            "score_threshold": float(gate_threshold),
            "threshold_selection": "calibration sensitivity >= 0.90, then maximum specificity",
            "calibration": {"n": int(np.sum(calibration)), **gate_calibration},
            "held_out_catalogue_references": gate_held,
            "held_out_artificial_protocol_positions": gate_artificial,
        },
    }
    (OUT / f"{args.cluster}_automatic_density_gate_diagnostic.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with (OUT / f"{args.cluster}_held_out_artificial_protocol_points.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(points[0]))
        writer.writeheader(); writer.writerows(points)

    fig, ax = plt.subplots(figsize=(6.6, 4.3), constrained_layout=True)
    ax.hist(proxy[calibration & ~label_high], bins=np.arange(proxy.max() + 2) - .5,
            alpha=.55, label="calibration low", color="#4C78A8", density=True)
    ax.hist(proxy[calibration & label_high], bins=np.arange(proxy.max() + 2) - .5,
            alpha=.55, label="calibration high", color="#E45756", density=True)
    ax.axvline(threshold, color="black", ls="--", lw=1.25, label=f"fixed threshold = {threshold:g}")
    ax.set(xlabel="Image-only candidates within 10 pixels", ylabel="Density", title=f"{args.cluster.upper()} single-feature density proxy")
    ax.legend(fontsize=8)
    fig.savefig(OUT / f"{args.cluster}_automatic_density_gate_proxy.png", dpi=240)
    plt.close(fig)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
