#!/usr/bin/env python
"""Common-protocol stratified HST curves and blind AstroCFR quality flags.

The reference catalogue is used only for the disclosed target-adaptation
partitions and for final evaluation.  Every exported quality flag is derived
from image pixels, candidate geometry, or classifier output and is therefore
available in a blind science field.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from astropy.table import Table
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for path in (HERE, REPO / "src" / "wpdc"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import hst_acsggct_benchmark as hst  # noqa: E402
import hst_epsf_deblend_artificial_stars as epsf  # noqa: E402
import hst_spatial_epsf_joint_pilot as spatial  # noqa: E402
import hst_unified_baseline_benchmark as bench  # noqa: E402
import real_data_domain_adaptation as adapt  # noqa: E402
import real_data_zero_shot_generalization as base  # noqa: E402
from quality_flags import BIT_DEFINITION, build_quality_bitmask  # noqa: E402

# The public release does not redistribute the large HST/CSST inputs.  During
# the manuscript build, discover the user-provided data tree recorded in the
# provenance manifest and redirect the established research modules to it.
if not hst.DATA.exists():
    data_hits = list(REPO.parent.glob("CSST_*/**/real_data_hst_acsggct"))
    cache_hits = list(REPO.parent.glob("CSST_*/**/real_data_generalization_results"))
    if len(data_hits) != 1 or len(cache_hits) != 1:
        raise RuntimeError(f"Expected one HST data tree and one simulation cache, got {data_hits} / {cache_hits}")
    hst.DATA = data_hits[0]
    base.OUT_DIR = cache_hits[0]

CLUSTERS = ("ngc6752", "ngc1851")
METHODS = ("dao", "sep", "photutils_psf", "wpdc_rf", "wpdc_epsf_deblend", "wpdc_spatial_epsf_joint")
LABEL = {
    "dao": "DAOStarFinder", "sep": "SEP/SExtractor-style",
    "photutils_psf": "Photutils PSFPhotometry", "wpdc_rf": "AstroCFR-RF",
    "wpdc_epsf_deblend": "AstroCFR ePSF-deblend",
    "wpdc_spatial_epsf_joint": "AstroCFR spatial-ePSF joint fit",
}


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return [None, None]
    p = k / n; den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [float(max(0, mid - half)), float(min(1, mid + half))]


def robust_rms(values):
    values = np.asarray(values, float)
    values = values[np.isfinite(values)]
    if len(values) < 5:
        return None
    med = np.median(values); mad = 1.4826 * np.median(np.abs(values - med))
    good = np.abs(values - med) <= max(3 * mad, 1e-5)
    if good.sum() < 5:
        return None
    return float(np.sqrt(np.mean((values[good] - np.mean(values[good])) ** 2)))


def source_snr(flux, rms, area=math.pi * 3.0**2):
    flux = np.asarray(flux, float)
    return flux / np.sqrt(np.maximum(flux, 0) + area * float(rms) ** 2 + 1e-12)


def measurement_context(det_global, flux, ref, ref_mag, ref_part):
    matched, ridx = hst.one_to_one(det_global, ref)
    chosen = np.flatnonzero(matched); ri = ridx[matched]
    train = ref_part[ri] != 2; test = ref_part[ri] == 2
    if train.sum() < 10 or test.sum() < 5:
        return matched, ridx, None
    coeff = hst.fit_affine(det_global[chosen][train], ref[ri][train])
    corrected = hst.apply_affine(det_global[chosen], coeff)
    delta = corrected - ref[ri]
    inst = -2.5 * np.log10(np.maximum(np.asarray(flux, float), 1e-12))
    zp = float(np.median(ref_mag[ri][train] - inst[chosen][train]))
    mag_resid = inst[chosen] + zp - ref_mag[ri]
    return matched, ridx, {"chosen": chosen, "ri": ri, "test": test,
                           "position_1d_px": np.hypot(delta[:, 0], delta[:, 1]) / math.sqrt(2),
                           "mag_resid": mag_resid}


def stratified_rows(cluster, method, xy, flux, runtime_s, rms):
    _, cat = hst.read_cluster(cluster)
    x, y, _, quality, _ = hst.catalog_subsets(cat)
    ref = np.c_[x[quality], y[quality]]
    ref_mag = np.asarray(cat["Vvega"], float)[quality]
    ref_part = hst.ref_cells(ref); test_ref = ref_part == 2
    tree = cKDTree(ref)
    density = np.asarray([len(tree.query_ball_point(point, 10)) - 1 for point in ref])
    det = np.asarray(xy, float) + np.array([hst.CROP_X0, hst.CROP_Y0])
    det_test = adapt.cell_ids(np.asarray(xy, float), 200) == 2
    snr = source_snr(flux, rms)
    matched, ridx, measure = measurement_context(det, flux, ref, ref_mag, ref_part)
    rows = []

    mag_bins = [("V<=18", -np.inf, 18), ("18<V<=20", 18, 20),
                ("20<V<=21", 20, 21), ("21<V<=22", 21, 22), ("V>22", 22, np.inf)]
    density_bins = [("0-1 neighbours", 0, 1), ("2 neighbours", 2, 2), (">=3 neighbours", 3, np.inf)]
    for axis, bins, values in (("magnitude", mag_bins, ref_mag), ("density", density_bins, density)):
        for label, lo, hi in bins:
            if axis == "magnitude": mask = test_ref & (values > lo) & (values <= hi)
            else: mask = test_ref & (values >= lo) & (values <= hi)
            recovered, _ = hst.one_to_one(det[det_test], ref[mask])
            n = int(mask.sum()); k = int(recovered.sum())
            row = {"cluster": cluster, "method": method, "label": LABEL[method], "axis": axis,
                   "bin": label, "n_reference": n, "recovered": k,
                   "completeness": None if n == 0 else k / n, "completeness_ci95": wilson(k, n),
                   "runtime_s_per_mpix": runtime_s / (hst.CROP_SIZE**2) * 1e6}
            if measure is not None:
                m = measure["test"] & mask[measure["ri"]]
                row["position_rms_mas"] = (None if m.sum() < 5 else
                    float(np.sqrt(np.mean(measure["position_1d_px"][m] ** 2)) * hst.PIXEL_SCALE_MAS))
                row["magnitude_rms_mag"] = robust_rms(measure["mag_resid"][m])
                row["n_measurement"] = int(m.sum())
            rows.append(row)

    snr_bins = [("SNR<5", -np.inf, 5), ("5<=SNR<10", 5, 10),
                ("10<=SNR<20", 10, 20), ("20<=SNR<50", 20, 50), ("SNR>=50", 50, np.inf)]
    for label, lo, hi in snr_bins:
        mask = det_test & (snr >= lo) & (snr < hi)
        dm, _ = hst.one_to_one(det[mask], ref)
        n = int(mask.sum()); k = int(dm.sum())
        row = {"cluster": cluster, "method": method, "label": LABEL[method], "axis": "detection_snr",
               "bin": label, "n_detection": n, "catalogue_matches": k,
               "catalogue_match_lower_bound": None if n == 0 else k / n,
               "catalogue_match_ci95": wilson(k, n),
               "runtime_s_per_mpix": runtime_s / (hst.CROP_SIZE**2) * 1e6}
        if measure is not None:
            m = measure["test"] & mask[measure["chosen"]]
            row["position_rms_mas"] = (None if m.sum() < 5 else
                float(np.sqrt(np.mean(measure["position_1d_px"][m] ** 2)) * hst.PIXEL_SCALE_MAS))
            row["magnitude_rms_mag"] = robust_rms(measure["mag_resid"][m])
            row["n_measurement"] = int(m.sum())
        rows.append(row)
    return rows


def local_fit_metrics(image, rms, grid, xy, flux):
    quality = np.full(len(xy), np.nan); improvement = np.full(len(xy), np.nan)
    for i, ((x, y), f) in enumerate(zip(xy, flux)):
        item = epsf.local_patch(image, x, y, half=5)
        if item is None or not np.isfinite(f) or f <= 0:
            continue
        patch, ix, iy = item; yy, xx = np.mgrid[iy-5:iy+6, ix-5:ix+6]
        edge = np.r_[patch[0], patch[-1], patch[:, 0], patch[:, -1]]; bkg = float(np.median(edge))
        model = bkg + f * epsf.psf_values(spatial.psf_at(grid, x, y), xx, yy, x, y)
        sse0 = float(np.sum((patch - bkg) ** 2)); sse = float(np.sum((patch - model) ** 2))
        quality[i] = math.sqrt(sse / patch.size) / max(float(rms), 1e-12)
        improvement[i] = 1.0 - sse / max(sse0, 1e-12)
    return quality, improvement


def blind_quality_catalogue(cluster, image, rms, rfctx, output_dir):
    src = base.detect_sources(image, rms, fwhm=2.2, threshold_sigma=3.0)
    initial = np.c_[np.asarray(src["xcentroid"], float), np.asarray(src["ycentroid"], float)]
    global_psf, _ = epsf.build_epsf(image, src)
    candidates, _, _, added = epsf.residual_candidates(image, rms, global_psf, initial)
    grid = spatial.build_quadrant_psfs(image, src)
    fitted, flux = spatial.fit_catalogue_spatial(image, grid, candidates, passes=2)
    good = np.isfinite(flux) & (flux > 0); fitted = fitted[good]; flux = flux[good]; candidates = candidates[good]
    tree = cKDTree(fitted)
    neighbours = np.asarray([len(tree.query_ball_point(point, 10)) - 1 for point in fitted], int)
    initial_distance, initial_index = cKDTree(initial).query(candidates, k=1)
    deblend = initial_distance > 1.0
    snr = source_snr(flux, rms)
    psf_quality, residual_improvement = local_fit_metrics(image, rms, grid, fitted, flux)

    # Saturation/bright-core proximity is deliberately image-derived.  The
    # threshold and radius are exported so downstream users can revise them.
    bright_threshold = float(max(np.nanpercentile(image, 99.995), 20 * rms))
    bright_yx = np.argwhere(image >= bright_threshold)
    if len(bright_yx):
        dbright, _ = cKDTree(bright_yx[:, ::-1]).query(fitted, k=1)
    else:
        dbright = np.full(len(fitted), np.inf)
    saturation_neighbour = dbright <= 10.0

    mod, clf, _, _, mean, std = rfctx
    X, _ = mod._extract_clf_features(src, image, rms)
    p_initial = clf.predict_proba((X - mean) / std)[:, 1]
    classifier_probability = np.where(initial_distance <= 1.5, p_initial[initial_index], np.nan)

    bitmask = build_quality_bitmask(snr, psf_quality, neighbours, saturation_neighbour,
                                    deblend, classifier_probability)
    tab = Table({
        "x": fitted[:, 0], "y": fitted[:, 1], "flux": flux, "snr": snr,
        "residual_improvement": residual_improvement, "neighbour_count_10px": neighbours,
        "deblend_flag": deblend, "saturation_neighbour_flag": saturation_neighbour,
        "classifier_probability": classifier_probability, "psf_fit_quality": psf_quality,
        "quality_bitmask": bitmask,
    })
    tab.meta.update({"cluster": cluster, "coordinate_frame": "central 1200x1200 crop",
                     "reference_catalogue_used": "no", "residual_candidates_added": int(added),
                     "bright_core_threshold": bright_threshold,
                     "bit_definition": "; ".join(f"{key} {value}" for key, value in BIT_DEFINITION.items())})
    path = output_dir / f"{cluster}_astrocfr_blind_quality_catalogue.ecsv"
    tab.write(path, format="ascii.ecsv", overwrite=True)
    return path, {"cluster": cluster, "sources": len(tab), "residual_candidates_added": int(added),
                  "flagged_low_snr": int(np.sum(snr < 5)), "flagged_poor_psf": int(np.sum(psf_quality > 3)),
                  "flagged_crowded": int(np.sum(neighbours >= 3)),
                  "flagged_bright_core": int(np.sum(saturation_neighbour),),
                  "flagged_residual_deblend": int(np.sum(deblend))}


def plot_curves(rows, output_dir):
    methods = list(METHODS); colors = plt.cm.tab10(np.linspace(0, 1, len(methods)))
    for cluster in CLUSTERS:
        fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
        for method, color in zip(methods, colors):
            mag = [r for r in rows if r["cluster"] == cluster and r["method"] == method and r["axis"] == "magnitude"]
            den = [r for r in rows if r["cluster"] == cluster and r["method"] == method and r["axis"] == "density"]
            snr = [r for r in rows if r["cluster"] == cluster and r["method"] == method and r["axis"] == "detection_snr"]
            axes[0, 0].plot(range(len(mag)), [r["completeness"] for r in mag], marker="o", color=color, label=LABEL[method])
            axes[0, 1].plot(range(len(den)), [r["completeness"] for r in den], marker="o", color=color)
            axes[1, 0].plot(range(len(snr)), [r["catalogue_match_lower_bound"] for r in snr], marker="o", color=color)
            axes[1, 1].plot(range(len(mag)), [np.nan if r.get("magnitude_rms_mag") is None else r["magnitude_rms_mag"] for r in mag], marker="o", color=color)
        axes[0, 0].set(title="Recovery by reference magnitude", ylabel="Completeness", xticks=range(5), xticklabels=[r["bin"] for r in mag], ylim=(0, 1.05))
        axes[0, 1].set(title="Recovery by local density", ylabel="Completeness", xticks=range(3), xticklabels=[r["bin"] for r in den], ylim=(0, 1.05))
        axes[1, 0].set(title="Catalogue-match lower bound by detection SNR", ylabel="Lower bound", xticks=range(5), xticklabels=[r["bin"] for r in snr], ylim=(0, 1.05))
        axes[1, 1].set(title="Conditional photometric RMS by magnitude", ylabel="RMS / mag", xticks=range(5), xticklabels=[r["bin"] for r in mag])
        for ax in axes.flat:
            ax.grid(alpha=.25); ax.tick_params(axis="x", rotation=20)
        axes[0, 0].legend(fontsize=7, ncol=2)
        fig.suptitle(cluster.upper() + " common-protocol stratification")
        fig.savefig(output_dir / f"{cluster}_stratified_recovery_precision.png", dpi=220)
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=REPO / "results" / "hst_stratified_quality")
    args = parser.parse_args(); args.output_dir.mkdir(parents=True, exist_ok=True)
    all_rows = []; flag_summaries = []
    for cluster in CLUSTERS:
        image, _ = hst.read_cluster(cluster); sub, rms = base.estimate_background(image)
        pre = base.detect_sources(sub, rms, fwhm=2.0, threshold_sigma=10)
        mod = adapt.load_pipeline(); fwhm = float(np.clip(mod.estimate_psf_fwhm(sub, pre, rms, min_snr=20, max_sources=40), 1.5, 4.0))
        rfctx = bench.prepare_wpdc_rf(cluster, sub, rms, fwhm)
        for method in METHODS:
            print(cluster, method, flush=True); start = time.perf_counter()
            xy, flux = bench.method_run(method, sub, rms, fwhm, rfctx)
            all_rows.extend(stratified_rows(cluster, method, xy, flux, time.perf_counter() - start, rms))
        _, summary = blind_quality_catalogue(cluster, sub, rms, rfctx, args.output_dir)
        flag_summaries.append(summary)
    fields = sorted({key for row in all_rows for key in row})
    with (args.output_dir / "stratified_recovery_precision.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(all_rows)
    (args.output_dir / "stratified_recovery_precision.json").write_text(json.dumps({"protocol": {
        "clusters": CLUSTERS, "methods": METHODS, "association_radius_px": 2,
        "spatial_test_partition": 2, "catalogue_match_is_lower_bound": True,
        "runtime": "single-run method-stage wall clock; use repeated benchmark for final absolute costs"
    }, "rows": all_rows, "blind_quality_catalogues": flag_summaries}, indent=2), encoding="utf-8")
    plot_curves(all_rows, args.output_dir)
    print(args.output_dir)


if __name__ == "__main__":
    main()
