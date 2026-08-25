#!/usr/bin/env python
"""Render auditable HST failure cases and density/magnitude recovery curves."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits
from astropy.table import Table
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT.parent / "CSST_上海电机学院" / "代码及中间过程文件" / "fanhuaxing"
DATA = WORK / "real_data_hst_acsggct"
OUT = ROOT / "results" / "hst_failure_cases"
CROP0 = 2400
CROP = 1200


def quality_catalog(cluster):
    cat = Table.read(next((DATA.glob(f"*{cluster}*rdviq*"))), format="ascii")
    x = np.asarray(cat["x"], float) - 1.0; y = np.asarray(cat["y"], float) - 1.0
    v = np.asarray(cat["Vvega"], float)
    inside = (x >= CROP0 + 12) & (x < CROP0 + CROP - 12) & (y >= CROP0 + 12) & (y < CROP0 + CROP - 12)
    q = (inside & (v < 90) & (np.asarray(cat["err"], float) < .10) &
         (np.asarray(cat["qfitV"], float) < .30) & (np.asarray(cat["othv"], float) < 1.0) &
         (np.asarray(cat["Nv"], int) >= 1))
    return np.column_stack([x[q] - CROP0, y[q] - CROP0]), v[q], cat


def candidates(cluster):
    t = Table.read(WORK / "hst_acsggct_benchmark_results" / f"{cluster}_wpdc_candidates.ecsv", format="ascii.ecsv")
    xy = np.column_stack([np.asarray(t["x"], float) - CROP0, np.asarray(t["y"], float) - CROP0])
    return xy, t


def asinh_image(cluster):
    path = next(DATA.glob(f"*{cluster}*f606w*v2_img.fits"))
    image = fits.getdata(path).astype(float)[CROP0:CROP0+CROP, CROP0:CROP0+CROP]
    finite = np.isfinite(image); med = np.nanmedian(image[finite]); scale = np.nanpercentile(np.abs(image[finite]-med), 85)
    return np.arcsinh((image-med)/max(scale, 1.0)), image


def match_failure(ref, mag, cand):
    tree = cKDTree(cand); dist, _ = tree.query(ref)
    rt = cKDTree(ref); density = np.array([len(rt.query_ball_point(p, 10))-1 for p in ref])
    return dist, density


def panel(ax, cluster, center, title, mask_ref, missed_mask, bright=None, candidate_mask=None):
    display, raw = asinh_image(cluster); cx, cy = center; half = 95
    x0, x1 = max(0, int(cx-half)), min(CROP, int(cx+half)); y0, y1 = max(0, int(cy-half)), min(CROP, int(cy+half))
    ax.imshow(display, origin="lower", cmap="gray", vmin=np.percentile(display, 2), vmax=np.percentile(display, 99.7))
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
    ref, mag, _ = quality_catalog(cluster); cand, _ = candidates(cluster)
    local = (ref[:, 0] >= x0) & (ref[:, 0] < x1) & (ref[:, 1] >= y0) & (ref[:, 1] < y1)
    ax.scatter(ref[local & mask_ref, 0], ref[local & mask_ref, 1], s=22, facecolors="none", edgecolors="#32d6ff", linewidths=.65, label="quality reference")
    miss = local & missed_mask
    ax.scatter(ref[miss, 0], ref[miss, 1], s=42, marker="x", c="#ff3b30", linewidths=1.1, label="missed reference")
    cd = (cand[:, 0] >= x0) & (cand[:, 0] < x1) & (cand[:, 1] >= y0) & (cand[:, 1] < y1)
    if candidate_mask is not None: cd &= candidate_mask
    ax.scatter(cand[cd, 0], cand[cd, 1], s=7, c="#ffd60a", alpha=.70, label="WPDC candidate")
    if bright is not None: ax.scatter([bright[0]], [bright[1]], s=100, facecolors="none", edgecolors="#ff9f0a", linewidths=1.2, label="bright-star core")
    ax.set_title(title, fontsize=9); ax.set_xlabel("local x / pixel"); ax.set_ylabel("local y / pixel"); ax.legend(fontsize=6, loc="upper right")


def render_failures():
    OUT.mkdir(parents=True, exist_ok=True)
    cases = {}
    # Case 1: densest missed V<=20 reference in NGC 6752.
    ref, mag, _ = quality_catalog("ngc6752"); cand, _ = candidates("ngc6752"); dist, density = match_failure(ref, mag, cand)
    high = (dist > 2) & (density >= 3) & (mag <= 20); idx = np.where(high)[0][np.argmax(density[high])]
    cases["high_crowding_miss"] = {"cluster": "ngc6752", "center": ref[idx].tolist(), "V": float(mag[idx]), "density_within_10px": int(density[idx]), "nearest_candidate_distance_px": float(dist[idx])}
    # Case 2: a missed reference nearest a bright V<=16 source in NGC 6752.
    bright = ref[mag <= 16]; bdist, bidx = cKDTree(bright).query(ref)
    near = (dist > 2) & (bdist >= 3) & (bdist < 30); idx2 = np.where(near)[0][np.argmin(bdist[near])]
    bright_xy = bright[bidx[idx2]]
    cases["bright_star_artifact"] = {"cluster": "ngc6752", "center": bright_xy.tolist(), "missed_reference": ref[idx2].tolist(), "bright_V": float(mag[mag <= 16][bidx[idx2]]), "missed_V": float(mag[idx2]), "distance_to_bright_core_px": float(bdist[idx2])}
    # Case 3: dense NGC 1851 held-out region with the largest adapted miss density.
    ref3, mag3, _ = quality_catalog("ngc1851"); cand3, tab3 = candidates("ngc1851"); dist3, density3 = match_failure(ref3, mag3, cand3)
    part = np.asarray(tab3["spatial_partition"], int) if "spatial_partition" in tab3.colnames else np.full(len(cand3), 2)
    heldout = part == 2
    # Show a test-region failure and report that it is held-out; reference miss is evaluated against retained candidates.
    hard = (dist3 > 2) & (density3 >= 3) & (mag3 <= 20); idx3 = np.where(hard)[0][np.argmax(density3[hard])]
    cases["domain_adaptation_failure"] = {"cluster": "ngc1851", "center": ref3[idx3].tolist(), "V": float(mag3[idx3]), "density_within_10px": int(density3[idx3]), "nearest_candidate_distance_px": float(dist3[idx3]), "candidate_test_partition_count": int(heldout.sum())}
    fig, axes = plt.subplots(1, 3, figsize=(14.5, 4.6), constrained_layout=True)
    panel(axes[0], "ngc6752", ref[idx].tolist(), "(a) High-crowding miss: NGC 6752", (mag <= 22), high)
    panel(axes[1], "ngc6752", bright_xy.tolist(), "(b) Bright-star artifact region: NGC 6752", (mag <= 22), (dist > 2), bright_xy)
    ref3_test = ((np.floor(ref3[:, 0] / 200).astype(int) + np.floor(ref3[:, 1] / 200).astype(int)) % 3) == 2
    cand3_test = np.asarray(tab3["spatial_partition"], int) == 2 if "spatial_partition" in tab3.colnames else None
    panel(axes[2], "ngc1851", ref3[idx3].tolist(), "(c) Domain-adaptation failure: NGC 1851", ref3_test & (mag3 <= 22), ref3_test & hard, candidate_mask=cand3_test)
    fig.savefig(OUT / "fig_failure_cases.png", dpi=260); plt.close(fig)
    (OUT / "failure_case_metrics.json").write_text(json.dumps(cases, indent=2), encoding="utf-8")


def render_stratified():
    source = WORK / "hst_expanded_artificial_ngc6752_results" / "expanded_artificial_recovery.csv"
    rows = list(csv.DictReader(source.open(encoding="utf-8")))
    labels = {r["method"]: r["label"] for r in rows}; methods = list(labels)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), constrained_layout=True)
    colors = plt.cm.tab10(np.linspace(0, 1, len(methods)))
    for ax, band in zip(axes, ("low", "high")):
        for color, method in zip(colors, methods):
            q = [r for r in rows if r["method"] == method and r["density_band"] == band]
            q.sort(key=lambda r: float(r["mag"]))
            x = [float(r["mag"]) for r in q]; y = [float(r["recovery"]) for r in q]
            lo = [json.loads(r["recovery_ci95"])[0] for r in q]; hi = [json.loads(r["recovery_ci95"])[1] for r in q]
            ax.plot(x, y, marker="o", linewidth=1.5, color=color, label=labels[method]); ax.fill_between(x, lo, hi, color=color, alpha=.10)
        ax.set_title(f"{band.capitalize()} density (0–1 vs ≥3 neighbours)"); ax.set_xlabel("Injected V magnitude (fainter →)"); ax.set_ylabel("Recovery within 2 pixels"); ax.set_ylim(0, 1.02); ax.set_xticks([20, 22]); ax.grid(alpha=.25)
    axes[1].legend(fontsize=7, loc="lower left")
    fig.savefig(OUT / "fig_density_magnitude_recovery.png", dpi=260); plt.close(fig)


if __name__ == "__main__":
    render_failures(); render_stratified(); print(OUT)
