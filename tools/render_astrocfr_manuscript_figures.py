#!/usr/bin/env python
"""Render the manuscript figures whose display labels changed to AstroCFR.

The numerical inputs are read from the registered result products. Historical
WPDC labels in those machine-readable products are intentionally left intact;
only publication-facing labels are mapped to AstroCFR.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits
from astropy.table import Table
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch
from scipy.spatial import cKDTree


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "astrocfr_manuscript_figures"
LEGACY = ROOT.parent / "CSST_上海电机学院" / "代码及中间过程文件" / "fanhuaxing"
UNIFIED = LEGACY / "hst_unified_baseline_results_v3" / "hst_unified_baseline_summary.json"
UNIFIED_ARTIFICIAL = LEGACY / "hst_unified_baseline_results" / "hst_unified_baseline_summary.json"
EXPANDED = ROOT / "results" / "hst_expanded_artificial_ngc6752" / "expanded_artificial_aggregate.csv"

METHOD_LABEL = {
    "dao": "DAO",
    "sep": "SEP",
    "photutils_psf": "Photutils PSF",
    "wpdc_rf": "AstroCFR-RF",
    "wpdc_epsf_deblend": "AstroCFR ePSF+deblend",
    "wpdc_spatial_epsf_joint": "AstroCFR spatial-ePSF+joint",
}
BAR_LABEL = {
    "dao": "DAO",
    "sep": "SEP",
    "photutils_psf": "Photutils\nPSF",
    "wpdc_rf": "AstroCFR\nRF",
    "wpdc_epsf_deblend": "AstroCFR ePSF\n+ deblend",
    "wpdc_spatial_epsf_joint": "spatial ePSF\n+ joint",
}
COLOR = {
    "dao": "#78889a",
    "sep": "#bb7b3f",
    "photutils_psf": "#6c78b8",
    "wpdc_rf": "#35a27b",
    "wpdc_epsf_deblend": "#d94c4c",
    "wpdc_spatial_epsf_joint": "#8b5fbf",
}
CANONICAL_RUNTIME = {
    "dao": 0.11,
    "sep": 5.18,
    "photutils_psf": 8.64,
    "wpdc_rf": 0.48,
    "wpdc_epsf_deblend": 24.24,
    "wpdc_spatial_epsf_joint": 32.23,
}


def display_label(text: str) -> str:
    return (text.replace("WPDC original (target-adapted RF)", "AstroCFR-RF")
            .replace("WPDC ePSF + residual deblend", "AstroCFR ePSF+deblend")
            .replace("WPDC spatial ePSF + joint fit", "AstroCFR spatial-ePSF+joint")
            .replace("WPDC-RF", "AstroCFR-RF")
            .replace("WPDC", "AstroCFR"))


def render_fig1() -> None:
    titles = [
        "Raw CSST-like\nchip image", "Adaptive\nbackground model",
        "Multi-branch\nsource detection", "Bright-star and\nblend recovery",
        "Point-source\nclassification", "Astrometric\nrefinement",
        "Photometric\ncalibration", "Final\ncatalogue",
    ]
    details = [
        "flat/dark/readout\nnoise-aware pixels", "2D background + RMS\nrobust interpolation",
        "DAO proposals +\nbranch thresholds", "mask, L1/L2,\nresidual deblending",
        "morphology +\nRF/CNN evidence", "WCS + polynomial/LUT\ncorrection",
        "aperture/ePSF flux +\nzero-point refinement", "positions, magnitudes,\nquality flags",
    ]
    fig, ax = plt.subplots(figsize=(12.8, 6.5), dpi=220)
    ax.set_xlim(0, 12.8); ax.set_ylim(0, 6.5); ax.axis("off")
    ax.text(6.4, 6.10, "AstroCFR end-to-end multimedia image-processing architecture",
            ha="center", va="center", fontsize=17, fontweight="bold")
    ax.text(.46, 5.46, "Candidate construction", fontsize=10.5,
            color="#4d5966", fontweight="bold")
    ax.text(.46, 2.73, "Candidate screening, measurement, and catalogue assembly",
            fontsize=10.5, color="#4d5966", fontweight="bold")
    width, height = 2.34, 1.44
    pos = [(0.40, 3.73), (3.42, 3.73), (6.44, 3.73), (9.46, 3.73),
           (9.46, .98), (6.44, .98), (3.42, .98), (0.40, .98)]
    for i, (title, detail) in enumerate(zip(titles, details)):
        x, y = pos[i]
        ax.add_patch(FancyBboxPatch((x, y), width, height,
                     boxstyle="round,pad=0.025,rounding_size=0.03",
                     linewidth=1.5, edgecolor="#1f5f8b", facecolor="#eaf2f8"))
        ax.text(x + width / 2, y + .96, title, ha="center", va="center",
                fontsize=11.0, fontweight="bold", linespacing=1.05)
        ax.plot([x + .16, x + width - .16], [y + .61, y + .61], color="#9eb7ca", lw=.8)
        ax.text(x + width / 2, y + .31, detail, ha="center", va="center",
                fontsize=8.4, color="#4d5966", linespacing=1.12)
    for i in range(3):
        x, y = pos[i]
        ax.annotate("", xy=(pos[i + 1][0] - .10, y + height / 2),
                    xytext=(x + width + .10, y + height / 2),
                    arrowprops={"arrowstyle": "-|>", "lw": 1.25, "color": "#2f3e4d"})
    ax.annotate("", xy=(pos[4][0] + width / 2, pos[4][1] + height + .08),
                xytext=(pos[3][0] + width / 2, pos[3][1] - .08),
                arrowprops={"arrowstyle": "-|>", "lw": 1.25, "color": "#2f3e4d"})
    for i in range(4, 7):
        x, y = pos[i]
        ax.annotate("", xy=(pos[i + 1][0] + width + .10, y + height / 2),
                    xytext=(x - .10, y + height / 2),
                    arrowprops={"arrowstyle": "-|>", "lw": 1.25, "color": "#2f3e4d"})
    ax.text(6.4, .32, "Simulation development → lightweight target adaptation → science-ready survey catalogue",
            ha="center", fontsize=10.6, color="#263746")
    fig.savefig(OUT / "fig1_astrocfr_architecture.png", dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def render_fig9() -> None:
    chips = ["Chip 12", "Chip 13", "Chip 17", "Chip 18"]
    sex_rec = [91.6, 87.6, 89.1, 83.7]
    ast_rec = [96.9, 93.1, 96.4, 91.8]
    sex_pre = [13.2, 21.6, 13.2, 22.8]
    ast_pre = [100.0] * 4
    x = np.arange(4); w = .36
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4), constrained_layout=True)
    for ax, a, b, metric in zip(axes, (sex_rec, sex_pre), (ast_rec, ast_pre), ("Recall", "Precision")):
        ax.bar(x - w / 2, a, width=w, color="#7d8b99", label="SExtractor")
        ax.bar(x + w / 2, b, width=w, color="#2f6b97", label="AstroCFR")
        ax.set_xticks(x, chips); ax.set_ylim(0, 108); ax.set_ylabel("Percentage (%)")
        ax.set_title(metric); ax.grid(axis="y", alpha=.2); ax.legend(fontsize=8)
    fig.suptitle("AstroCFR improves dense-field purity relative to calibrated SExtractor",
                 fontsize=13, fontweight="bold")
    fig.savefig(OUT / "fig9_astrocfr_sextractor_comparison.png", dpi=220, facecolor="white")
    plt.close(fig)


def _image2d(path: Path) -> np.ndarray:
    with fits.open(path) as hdul:
        return np.asarray(next(h.data for h in hdul if h.data is not None), float).squeeze()


def render_fig14() -> None:
    data_dir = LEGACY / "real_data"
    result_dir = LEGACY / "real_data_generalization_results"
    domains = [("PS1_M31_i", "Pan-STARRS1 / M31"), ("LS_DR10_M13_r", "Legacy Survey DR10 / M13")]
    fig, axes = plt.subplots(1, 2, figsize=(12.0, 5.25), constrained_layout=True)
    for ax, (name, title) in zip(axes, domains):
        image = _image2d(data_dir / f"{name}.fits")
        cand = Table.read(result_dir / f"{name}_candidates.ecsv", format="ascii.ecsv")
        gaia = Table.read(result_dir / f"{name}_gaia_reference.ecsv", format="ascii.ecsv")
        keep = np.asarray(cand["retained"], bool)
        lo, hi = np.nanpercentile(image, [5, 99.5])
        ax.imshow(image, origin="lower", cmap="gray", vmin=lo, vmax=hi)
        ax.scatter(gaia["x"], gaia["y"], s=12, facecolors="none", edgecolors="#35c6ff",
                   linewidths=.6, label="Gaia DR3 G≤20")
        ax.scatter(np.asarray(cand["x"])[keep], np.asarray(cand["y"])[keep], s=5,
                   c="#ffbf2f", alpha=.7, label="frozen AstroCFR filter")
        ax.set_title(title); ax.set_xlabel("x / pixel"); ax.set_ylabel("y / pixel")
        ax.legend(loc="upper right", fontsize=7)
    fig.savefig(OUT / "fig14_astrocfr_zero_shot.png", dpi=220, facecolor="white")
    plt.close(fig)


def _quality_reference(cluster: str) -> tuple[np.ndarray, np.ndarray]:
    path = LEGACY / "real_data_hst_acsggct" / f"hlsp_acsggct_hst_acs-wfc_{cluster}_r.rdviq.cal.adj.zpt"
    cat = Table.read(path, format="ascii")
    x = np.asarray(cat["x"], float) - 1.0; y = np.asarray(cat["y"], float) - 1.0
    inside = (x >= 2412) & (x < 3588) & (y >= 2412) & (y < 3588)
    q = (inside & (np.asarray(cat["Vvega"], float) < 90) &
         (np.asarray(cat["err"], float) < .10) & (np.asarray(cat["qfitV"], float) < .30) &
         (np.asarray(cat["othv"], float) < 1.0) & (np.asarray(cat["Nv"], int) >= 1))
    xy = np.column_stack([x[q], y[q]])
    local = xy - np.array([2400.0, 2400.0])
    part = ((np.floor(local[:, 0] / 200).astype(int) + np.floor(local[:, 1] / 200).astype(int)) % 3)
    return local, part


def render_fig16() -> None:
    result_dir = LEGACY / "hst_acsggct_benchmark_results"
    data_dir = LEGACY / "real_data_hst_acsggct"
    summary = json.loads((result_dir / "hst_acsggct_benchmark_summary.json").read_text(encoding="utf-8"))
    thresholds = {r["cluster"]: r["threshold"] for r in summary["results"] if r["mode"] == "target_adapted"}
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.4), constrained_layout=True)
    for ax, cluster in zip(axes, ("ngc6397", "ngc6752")):
        full = _image2d(data_dir / f"hlsp_acsggct_hst_acs-wfc_{cluster}_f606w_v2_img.fits")
        image = full[2400:3600, 2400:3600]
        cand = Table.read(result_dir / f"{cluster}_wpdc_candidates.ecsv", format="ascii.ecsv")
        keep = np.asarray(cand["adapted_probability"], float) >= thresholds[cluster]
        x = np.asarray(cand["x"], float)[keep] - 2400
        y = np.asarray(cand["y"], float)[keep] - 2400
        ref, part = _quality_reference(cluster)
        lo, hi = np.nanpercentile(image, [5, 99.7])
        ax.imshow(image, origin="lower", cmap="gray", vmin=lo, vmax=hi)
        test = part == 2
        ax.scatter(ref[test, 0], ref[test, 1], s=5, facecolors="none", edgecolors="#38c8ff",
                   linewidths=.35, label="official test references")
        ax.scatter(x, y, s=3, c="#ffbd2e", alpha=.65, label="AstroCFR adapted")
        ax.set_title(cluster.upper() + " central 1200×1200")
        ax.set_xlabel("x / pixel"); ax.set_ylabel("y / pixel"); ax.legend(fontsize=7)
    fig.savefig(OUT / "fig16_astrocfr_hst_benchmark.png", dpi=220, facecolor="white")
    plt.close(fig)


def render_controlled(data: dict, methods: list[str], filename: str) -> None:
    res = data["results"]
    fig, ax = plt.subplots(2, 2, figsize=(11.8, 8.0), constrained_layout=True)
    for row, cluster in enumerate(("ngc6397", "ngc6752")):
        r = {x["method"]: x for x in res if x["cluster"] == cluster}
        active = [m for m in methods if m in r]
        x = np.arange(len(active)); vals = [r[m]["test_completeness"] for m in active]
        lo = [v - r[m]["test_completeness_ci95"][0] for m, v in zip(active, vals)]
        hi = [r[m]["test_completeness_ci95"][1] - v for m, v in zip(active, vals)]
        ax[row, 0].bar(x, vals, color=[COLOR[m] for m in active], yerr=np.array([lo, hi]), capsize=3)
        ax[row, 0].set_ylim(0, 1.08); ax[row, 0].set_ylabel("completeness")
        ax[row, 0].set_title(cluster.upper() + " completeness (untouched test)")
        ax[row, 0].set_xticks(x, [BAR_LABEL[m] for m in active], fontsize=8.0)
        ax[row, 1].scatter([CANONICAL_RUNTIME[m] for m in active],
                           [r[m]["high_density_v20_recall"] for m in active],
                           s=85, c=[COLOR[m] for m in active])
        handles = [Line2D([0], [0], marker="o", linestyle="", markersize=7,
                          markerfacecolor=COLOR[m], markeredgecolor=COLOR[m], label=METHOD_LABEL[m])
                   for m in active]
        ax[row, 1].legend(handles=handles, loc="lower right", fontsize=6.1, ncol=2,
                          frameon=True, handletextpad=.35, columnspacing=.75)
        ax[row, 1].set_xscale("log"); ax[row, 1].set_xlim(.05, 100); ax[row, 1].set_ylim(0, 1.05)
        ax[row, 1].set_xlabel("runtime / s MPix$^{-1}$ (log)")
        ax[row, 1].set_ylabel("high-density V≤20 recall")
        ax[row, 1].set_title(cluster.upper() + f" high-density test (n={r['dao']['high_density_v20_n']})")
    fig.savefig(OUT / filename, dpi=240, facecolor="white")
    plt.close(fig)


def render_artificial(data: dict) -> None:
    methods = ["dao", "sep", "photutils_psf", "wpdc_rf", "wpdc_epsf_deblend"]
    inj = data["artificial_aggregate"]
    fig, axes = plt.subplots(1, 2, figsize=(11.8, 4.6), sharey=True, constrained_layout=True)
    for ax, cluster in zip(axes, ("ngc6397", "ngc6752")):
        for method in methods:
            rows = [x for x in inj if x["cluster"] == cluster and x["method"] == method]
            for band, ls in (("low", "-"), ("high", "--")):
                q = sorted([x for x in rows if x["density_band"] == band], key=lambda x: x["mag"])
                if not q:
                    continue
                xx = np.array([x["mag"] for x in q]); yy = np.array([x["recovery"] for x in q])
                lo = np.array([y - x["recovery_ci95"][0] for x, y in zip(q, yy)])
                hi = np.array([x["recovery_ci95"][1] - y for x, y in zip(q, yy)])
                ax.errorbar(xx, yy, yerr=np.array([lo, hi]), color=COLOR[method], linestyle=ls,
                            marker="o", label=METHOD_LABEL[method] + (" low" if band == "low" else " high"))
        ax.set_title(cluster.upper() + " artificial-star recovery"); ax.set_xlabel("Injected V magnitude")
        ax.set_xticks([20, 22]); ax.set_ylim(0, 1.05); ax.grid(alpha=.25)
        ax.legend(fontsize=5.8, ncol=2)
    axes[0].set_ylabel("Recovery (Wilson 95% CI)")
    fig.savefig(OUT / "fig19_astrocfr_artificial_recovery.png", dpi=240, facecolor="white")
    plt.close(fig)


def _expanded_rows() -> list[dict]:
    with EXPANDED.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def render_density_curves(rows: list[dict], include_spatial: bool, filename: str) -> None:
    labels = []
    for row in rows:
        label = display_label(row["method"])
        if (not include_spatial) and "spatial" in label.lower():
            continue
        if label not in labels:
            labels.append(label)
    colors = plt.cm.tab10(np.linspace(0, 1, len(labels)))
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.7), constrained_layout=True)
    for ax, band in zip(axes, ("low", "high")):
        for color, label in zip(colors, labels):
            q = [r for r in rows if display_label(r["method"]) == label and r["density_band"] == band]
            q.sort(key=lambda r: float(r["mag"]))
            x = [float(r["mag"]) for r in q]; y = [float(r["recovery"]) for r in q]
            lo = [float(r["ci95_low"]) for r in q]; hi = [float(r["ci95_high"]) for r in q]
            ax.plot(x, y, marker="o", linewidth=1.5, color=color, label=label)
            ax.fill_between(x, lo, hi, color=color, alpha=.10)
        # Use plain ASCII labels in the raster source so PDF/Word text extraction
        # cannot turn Unicode en-dashes/arrows/inequalities into mojibake.
        ax.set_title("Low density (0-1 neighbours)" if band == "low" else "High density (>=3 neighbours)")
        ax.set_xlabel("Injected V magnitude (fainter ->)"); ax.set_ylabel("Recovery within 2 pixels")
        ax.set_ylim(0, 1.02); ax.set_xticks([20, 22]); ax.grid(alpha=.25)
    axes[1].legend(fontsize=6.5, loc="lower left")
    fig.savefig(OUT / filename, dpi=240, facecolor="white")
    plt.close(fig)


def _failure_inputs(cluster: str):
    data_dir = LEGACY / "real_data_hst_acsggct"
    image = _image2d(data_dir / f"hlsp_acsggct_hst_acs-wfc_{cluster}_f606w_v2_img.fits")[2400:3600, 2400:3600]
    ref, _ = _quality_reference(cluster)
    cat = Table.read(data_dir / f"hlsp_acsggct_hst_acs-wfc_{cluster}_r.rdviq.cal.adj.zpt", format="ascii")
    x = np.asarray(cat["x"], float) - 1.0; y = np.asarray(cat["y"], float) - 1.0
    inside = (x >= 2412) & (x < 3588) & (y >= 2412) & (y < 3588)
    q = (inside & (np.asarray(cat["Vvega"], float) < 90) &
         (np.asarray(cat["err"], float) < .10) & (np.asarray(cat["qfitV"], float) < .30) &
         (np.asarray(cat["othv"], float) < 1.0) & (np.asarray(cat["Nv"], int) >= 1))
    mag = np.asarray(cat["Vvega"], float)[q]
    t = Table.read(LEGACY / "hst_acsggct_benchmark_results" / f"{cluster}_wpdc_candidates.ecsv", format="ascii.ecsv")
    cand = np.column_stack([np.asarray(t["x"], float) - 2400, np.asarray(t["y"], float) - 2400])
    return image, ref, mag, cand, t


def _failure_panel(ax, image, ref, cand, center, title, ref_mask, miss_mask, bright=None, cand_mask=None):
    finite = np.isfinite(image); med = np.nanmedian(image[finite]); scale = np.nanpercentile(np.abs(image[finite] - med), 85)
    display = np.arcsinh((image - med) / max(scale, 1.0))
    cx, cy = center; half = 95
    x0, x1 = max(0, int(cx - half)), min(1200, int(cx + half)); y0, y1 = max(0, int(cy - half)), min(1200, int(cy + half))
    ax.imshow(display, origin="lower", cmap="gray", vmin=np.nanpercentile(display, 2), vmax=np.nanpercentile(display, 99.7))
    ax.set_xlim(x0, x1); ax.set_ylim(y0, y1)
    local = (ref[:, 0] >= x0) & (ref[:, 0] < x1) & (ref[:, 1] >= y0) & (ref[:, 1] < y1)
    ax.scatter(ref[local & ref_mask, 0], ref[local & ref_mask, 1], s=22, facecolors="none",
               edgecolors="#32d6ff", linewidths=.65, label="quality reference")
    ax.scatter(ref[local & miss_mask, 0], ref[local & miss_mask, 1], s=42, marker="x",
               c="#ff3b30", linewidths=1.1, label="missed reference")
    cd = (cand[:, 0] >= x0) & (cand[:, 0] < x1) & (cand[:, 1] >= y0) & (cand[:, 1] < y1)
    if cand_mask is not None:
        cd &= cand_mask
    ax.scatter(cand[cd, 0], cand[cd, 1], s=7, c="#ffd60a", alpha=.70, label="AstroCFR candidate")
    if bright is not None:
        ax.scatter([bright[0]], [bright[1]], s=100, facecolors="none", edgecolors="#ff9f0a",
                   linewidths=1.2, label="bright-star core")
    ax.set_title(title, fontsize=9); ax.set_xlabel("local x / pixel"); ax.set_ylabel("local y / pixel")
    ax.legend(fontsize=6, loc="upper right")


def render_fig23() -> None:
    image, ref, mag, cand, _ = _failure_inputs("ngc6752")
    dist, _ = cKDTree(cand).query(ref); rt = cKDTree(ref)
    density = np.array([len(rt.query_ball_point(p, 10)) - 1 for p in ref])
    high = (dist > 2) & (density >= 3) & (mag <= 20)
    idx = np.where(high)[0][np.argmax(density[high])]
    bright = ref[mag <= 16]; bmag = mag[mag <= 16]
    bdist, bidx = cKDTree(bright).query(ref)
    near = (dist > 2) & (bdist >= 3) & (bdist < 30)
    idx2 = np.where(near)[0][np.argmin(bdist[near])]; bright_xy = bright[bidx[idx2]]
    image3, ref3, mag3, cand3, tab3 = _failure_inputs("ngc1851")
    dist3, _ = cKDTree(cand3).query(ref3); rt3 = cKDTree(ref3)
    density3 = np.array([len(rt3.query_ball_point(p, 10)) - 1 for p in ref3])
    hard3 = (dist3 > 2) & (density3 >= 3) & (mag3 <= 20)
    idx3 = np.where(hard3)[0][np.argmax(density3[hard3])]
    ref3_part = ((np.floor(ref3[:, 0] / 200).astype(int) + np.floor(ref3[:, 1] / 200).astype(int)) % 3)
    cand3_test = np.asarray(tab3["spatial_partition"], int) == 2
    fig, axes = plt.subplots(1, 3, figsize=(14.5, 4.6), constrained_layout=True)
    _failure_panel(axes[0], image, ref, cand, ref[idx], "(a) High-crowding miss: NGC 6752", mag <= 22, high)
    _failure_panel(axes[1], image, ref, cand, bright_xy, "(b) Bright-star artifact region: NGC 6752", mag <= 22, dist > 2, bright_xy)
    _failure_panel(axes[2], image3, ref3, cand3, ref3[idx3], "(c) Domain-adaptation failure: NGC 1851",
                   (ref3_part == 2) & (mag3 <= 22), (ref3_part == 2) & hard3, cand_mask=cand3_test)
    fig.savefig(OUT / "fig23_astrocfr_failure_cases.png", dpi=260, facecolor="white")
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = json.loads(UNIFIED.read_text(encoding="utf-8"))
    artificial_data = json.loads(UNIFIED_ARTIFICIAL.read_text(encoding="utf-8"))
    render_fig1(); render_fig9(); render_fig14(); render_fig16()
    render_controlled(data, ["dao", "sep", "photutils_psf", "wpdc_rf", "wpdc_epsf_deblend"],
                      "fig18_astrocfr_controlled_comparison.png")
    render_artificial(artificial_data)
    render_controlled(data, ["dao", "sep", "photutils_psf", "wpdc_rf", "wpdc_epsf_deblend", "wpdc_spatial_epsf_joint"],
                      "fig20_astrocfr_six_branch_comparison.png")
    rows = _expanded_rows()
    render_density_curves(rows, False, "fig21_astrocfr_density_recovery.png")
    render_fig23()
    render_density_curves(rows, True, "fig24_astrocfr_density_magnitude_recovery.png")
    print(OUT)


if __name__ == "__main__":
    main()
