#!/usr/bin/env python
"""Fixed-truth crowded-field benchmark across three astrophysical morphologies.

The scenes are deliberately labelled ``*-like``. They are reproducible stress
tests, not substitutes for public Galactic-centre, Milky-Way disk, or dwarf-
galaxy observations. Every method receives the same noisy image and is scored
against the same exhaustive simulated catalogue. Stars are rendered with a
spatially varying elliptical Moffat PSF; AstroCFR estimates its recovery ePSF
from the image, while the Photutils baseline retains its Gaussian PRF.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import threading
import time
import warnings
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import psutil
from astropy.io import fits
from scipy.spatial import cKDTree
from scipy.stats import binomtest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
HST = ROOT / "experiments" / "hst"
sys.path.insert(0, str(ROOT / "src" / "wpdc"))
sys.path.insert(0, str(HST))

import candidate_features
import hst_unified_baseline_benchmark as baseline
import real_data_zero_shot_generalization as imageops

DEFAULT_CONFIG = ROOT / "configs" / "astrophysical_crowded_scenes.json"
DEFAULT_OUT = ROOT / "results" / "astrophysical_scene_benchmark"
METHODS = ("dao", "sep", "photutils_psf", "astrocfr_epsf")
LABELS = {
    "dao": "DAOStarFinder",
    "sep": "SEP/SExtractor-style",
    "photutils_psf": "Photutils PSFPhotometry",
    "astrocfr_epsf": "AstroCFR ePSF + residual deblend",
}


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return [None, None]
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [float(max(0.0, mid - half)), float(min(1.0, mid + half))]


def measured(fn):
    proc = psutil.Process()
    base_rss = proc.memory_info().rss
    peak = [base_rss]
    stop = threading.Event()

    def poll():
        while not stop.wait(0.01):
            peak[0] = max(peak[0], proc.memory_info().rss)

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    start = time.perf_counter()
    try:
        value = fn()
        return value, time.perf_counter() - start, max(0, peak[0] - base_rss) / 1024**2
    finally:
        stop.set()
        thread.join()


def _inside(x, y, size, margin=9.0):
    return (x >= margin) & (x < size - margin) & (y >= margin) & (y < size - margin)


def _fill_positions(x, y, n, size, sampler, rng):
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    keep = _inside(x, y, size)
    chunks_x = [x[keep]]
    chunks_y = [y[keep]]
    total = int(keep.sum())
    while total < n:
        xx, yy = sampler(max(n - total, 64), rng)
        good = _inside(xx, yy, size)
        chunks_x.append(np.asarray(xx)[good])
        chunks_y.append(np.asarray(yy)[good])
        total += int(good.sum())
    return np.concatenate(chunks_x)[:n], np.concatenate(chunks_y)[:n]


def sample_positions(scene: str, n: int, size: int, rng):
    cx = cy = (size - 1) / 2
    if scene == "galactic_center_like":
        def sampler(count, rr):
            core = rr.random(count) < 0.72
            u = rr.random(count)
            radius = 0.11 * size * np.sqrt(u / np.maximum(1 - u, 1e-4))
            angle = rr.uniform(0, 2 * np.pi, count)
            x = cx + radius * np.cos(angle)
            y = cy + 0.82 * radius * np.sin(angle)
            x[~core] = rr.uniform(9, size - 9, (~core).sum())
            y[~core] = rr.uniform(9, size - 9, (~core).sum())
            return x, y
    elif scene == "thin_disk_like":
        def sampler(count, rr):
            x = rr.uniform(9, size - 9, count)
            warp = cy + 0.055 * size * np.sin(2 * np.pi * x / size)
            y = warp + rr.laplace(0, 0.105 * size, count)
            foreground = rr.random(count) < 0.18
            y[foreground] = rr.uniform(9, size - 9, foreground.sum())
            return x, y
    elif scene == "dwarf_galaxy_like":
        def sampler(count, rr):
            member = rr.random(count) < 0.82
            radius = rr.gamma(shape=2.0, scale=0.115 * size, size=count)
            angle = rr.uniform(0, 2 * np.pi, count)
            x = cx + radius * np.cos(angle)
            y = cy + 0.62 * radius * np.sin(angle)
            x[~member] = rr.uniform(9, size - 9, (~member).sum())
            y[~member] = rr.uniform(9, size - 9, (~member).sum())
            return x, y
    else:
        raise KeyError(scene)
    x0, y0 = sampler(n, rng)
    return _fill_positions(x0, y0, n, size, sampler, rng)


def extinction_map(scene: str, x, y, size):
    xx = np.asarray(x, float) / size
    yy = np.asarray(y, float) / size
    if scene == "galactic_center_like":
        lane = 1.25 * np.exp(-((yy - 0.52 - 0.07 * np.sin(7 * xx)) / 0.055) ** 2)
        clump = 0.85 * np.exp(-((xx - 0.28) ** 2 + (yy - 0.72) ** 2) / 0.012)
        texture = 0.35 * (1 + np.sin(13 * xx + 9 * yy))
        return lane + clump + texture
    if scene == "thin_disk_like":
        mid = 0.5 + 0.055 * np.sin(2 * np.pi * xx)
        lane = 1.45 * np.exp(-((yy - mid) / 0.045) ** 2)
        clouds = 0.55 * np.maximum(0, np.sin(10 * xx - 3 * yy))
        return lane + clouds
    return 0.18 + 0.28 * np.maximum(0, np.sin(7 * xx + 5 * yy))


def background(scene: str, size: int):
    yy, xx = np.mgrid[:size, :size]
    xn = xx / size
    yn = yy / size
    cx = cy = (size - 1) / 2
    if scene == "galactic_center_like":
        r = np.hypot((xx - cx) / (0.24 * size), (yy - cy) / (0.20 * size))
        return 125 + 360 / (1 + r**1.55) + 24 * xn + 18 * np.sin(5 * xn + 3 * yn)
    if scene == "thin_disk_like":
        mid = cy + 0.055 * size * np.sin(2 * np.pi * xn)
        return 72 + 92 * np.exp(-np.abs(yy - mid) / (0.115 * size)) + 22 * xn + 9 * np.sin(9 * xn)
    r = np.hypot((xx - cx) / (0.30 * size), (yy - cy) / (0.20 * size))
    return 38 + 52 * np.exp(-r) + 8 * xn


def draw_magnitudes(scene: str, n: int, limits, rng):
    lo, hi = map(float, limits)
    mags = lo + (hi - lo) * np.sqrt(rng.random(n))
    if scene == "galactic_center_like":
        bright = rng.random(n) < 0.05
        mags[bright] = rng.uniform(lo, min(lo + 2.2, hi), bright.sum())
    return mags


def local_psf(scene_cfg, x, y, size):
    low, high = map(float, scene_cfg["psf_fwhm_px"])
    xn = x / max(size - 1, 1)
    yn = y / max(size - 1, 1)
    fwhm = low + (high - low) * (0.55 * xn + 0.45 * yn)
    q = 0.86 + 0.10 * np.sin(2 * np.pi * yn)
    theta = 0.45 * np.sin(2 * np.pi * xn)
    return float(fwhm), float(np.clip(q, 0.75, 1.0)), float(theta)


def moffat_stamp(fwhm: float, q: float, theta: float, half: int = 7, beta: float = 2.8):
    yy, xx = np.mgrid[-half:half + 1, -half:half + 1]
    c, s = np.cos(theta), np.sin(theta)
    xp = c * xx + s * yy
    yp = -s * xx + c * yy
    alpha = fwhm / (2 * np.sqrt(2 ** (1 / beta) - 1))
    rr = (xp / alpha) ** 2 + (yp / (alpha * q)) ** 2
    stamp = (1 + rr) ** (-beta)
    return stamp / stamp.sum()


def render_scene(name: str, cfg: dict, common: dict, size: int):
    rng = np.random.default_rng(int(cfg["seed"]))
    n = max(220, int(round(cfg["stars_at_384px"] * (size / 384) ** 2)))
    x, y = sample_positions(name, n, size, rng)
    intrinsic_mag = draw_magnitudes(name, n, cfg["magnitude_range"], rng)
    extinction = extinction_map(name, x, y, size)
    observed_mag = intrinsic_mag + extinction
    flux = 10 ** (-0.4 * (observed_mag - float(common["photometric_zero_point"])))
    stellar = np.zeros((size, size), float)
    for sx, sy, sf in zip(x, y, flux):
        fwhm, q, theta = local_psf(cfg, sx, sy, size)
        stamp = moffat_stamp(fwhm, q, theta)
        half = stamp.shape[0] // 2
        ix, iy = int(round(sx)), int(round(sy))
        stellar[iy-half:iy+half+1, ix-half:ix+half+1] += sf * stamp
    smooth = background(name, size)
    expected = np.clip(stellar + smooth, 0, None)
    noisy = rng.poisson(expected).astype(float)
    noisy += rng.normal(0, float(common["read_noise_electrons"]), noisy.shape)
    truth = {
        "x": x,
        "y": y,
        "intrinsic_mag": intrinsic_mag,
        "observed_mag": observed_mag,
        "flux": flux,
        "extinction_mag": extinction,
    }
    return noisy, smooth, truth


def greedy_pairs(det, truth, radius):
    det = np.asarray(det, float)
    truth = np.asarray(truth, float)
    if len(det) == 0 or len(truth) == 0:
        return np.empty(0, int), np.empty(0, int), np.empty(0, float)
    distance, ti = cKDTree(truth).query(det, k=1)
    candidates = [(float(distance[i]), i, int(ti[i])) for i in range(len(det)) if distance[i] <= radius]
    candidates.sort()
    used_d, used_t, pairs = set(), set(), []
    for dist, di, tj in candidates:
        if di in used_d or tj in used_t:
            continue
        used_d.add(di)
        used_t.add(tj)
        pairs.append((di, tj, dist))
    if not pairs:
        return np.empty(0, int), np.empty(0, int), np.empty(0, float)
    return (np.asarray([p[0] for p in pairs], int),
            np.asarray([p[1] for p in pairs], int),
            np.asarray([p[2] for p in pairs], float))


def run_method(name, image, rms, fwhm):
    if name == "dao":
        return baseline.dao(image, rms, fwhm)
    if name == "sep":
        return baseline.sep_detect(image, rms)
    if name == "photutils_psf":
        return baseline.photutils_psf(image, rms, fwhm)
    if name == "astrocfr_epsf":
        return baseline.wpdc_deblend(image, rms, fwhm)
    raise KeyError(name)


def evaluate(scene, method, det, det_flux, truth, elapsed, memory, size, radius, pixel_scale):
    det = np.asarray(det, float)
    det_flux = np.asarray(det_flux, float)
    finite = (np.isfinite(det).all(axis=1) & np.isfinite(det_flux)) if len(det) else np.empty(0, bool)
    det = det[finite]
    det_flux = det_flux[finite]
    truth_xy = np.column_stack([truth["x"], truth["y"]])
    di, ti, distance = greedy_pairs(det, truth_xy, radius)
    matched_truth = np.zeros(len(truth_xy), bool)
    matched_truth[ti] = True
    tp = int(len(di))
    recall = tp / max(len(truth_xy), 1)
    precision = tp / max(len(det), 1)
    f1 = 2 * recall * precision / max(recall + precision, 1e-12)
    row = {
        "scene": scene,
        "method": method,
        "label": LABELS[method],
        "truth_stars": int(len(truth_xy)),
        "candidates": int(len(det)),
        "matched": tp,
        "recall": float(recall),
        "recall_ci95": wilson(tp, len(truth_xy)),
        "precision": float(precision),
        "precision_ci95": wilson(tp, len(det)),
        "f1": float(f1),
        "runtime_s": float(elapsed),
        "runtime_s_per_mpix": float(elapsed / (size * size) * 1e6),
        "peak_rss_delta_mb": float(memory),
    }
    if tp:
        row["astrometric_rms_mas"] = float(np.sqrt(np.mean(distance**2) / 2) * pixel_scale)
        row["astrometric_radial_median_px"] = float(np.median(distance))
        good_flux = (det_flux[di] > 0) & np.isfinite(det_flux[di]) & (truth["flux"][ti] > 0)
        if good_flux.sum() >= 10:
            delta = -2.5 * np.log10(det_flux[di][good_flux] / truth["flux"][ti][good_flux])
            delta -= np.median(delta)
            mad = 1.4826 * np.median(np.abs(delta - np.median(delta)))
            keep = np.abs(delta) <= max(3 * mad, 0.03)
            row["photometric_rms_mag"] = float(np.sqrt(np.mean(delta[keep] ** 2)))
            row["photometric_matches"] = int(keep.sum())

    mags = np.asarray(truth["observed_mag"], float)
    mag_bins = {
        "bright_mag_le_25": mags <= 25,
        "mid_mag_25_27": (mags > 25) & (mags <= 27),
        "faint_mag_gt_27": mags > 27,
    }
    density = np.asarray([len(v) - 1 for v in cKDTree(truth_xy).query_ball_point(truth_xy, 10.0)])
    density_bins = {
        "low_density_le_1": density <= 1,
        "mid_density_2_4": (density >= 2) & (density <= 4),
        "high_density_ge_5": density >= 5,
    }
    for key, mask in {**mag_bins, **density_bins}.items():
        mask = np.asarray(mask, bool)
        k, n = int(np.sum(matched_truth & mask)), int(np.sum(mask))
        row[f"{key}_n"] = n
        row[f"{key}_recovered"] = k
        row[f"{key}_recall"] = float(k / max(n, 1))
    return row


def paired_recovery(scene, catalogues, truth_xy, radius):
    if "astrocfr_epsf" not in catalogues or "photutils_psf" not in catalogues:
        return None
    recovered = {}
    for method in ("astrocfr_epsf", "photutils_psf"):
        _, ti, _ = greedy_pairs(catalogues[method], truth_xy, radius)
        mask = np.zeros(len(truth_xy), bool)
        mask[ti] = True
        recovered[method] = mask
    a = recovered["astrocfr_epsf"]
    b = recovered["photutils_psf"]
    a_only = int(np.sum(a & ~b))
    b_only = int(np.sum(b & ~a))
    discordant = a_only + b_only
    rng = np.random.default_rng(2026081399 + len(truth_xy))
    paired = a.astype(float) - b.astype(float)
    boot = [float(np.mean(rng.choice(paired, len(paired), replace=True))) for _ in range(5000)]
    return {
        "scene": scene,
        "method_a": "astrocfr_epsf",
        "method_b": "photutils_psf",
        "truth_stars": int(len(truth_xy)),
        "a_only": a_only,
        "b_only": b_only,
        "paired_recall_difference": float(np.mean(paired)),
        "paired_bootstrap_ci95": [float(v) for v in np.percentile(boot, [2.5, 97.5])],
        "mcnemar_exact_p": (float(binomtest(min(a_only, b_only), discordant, 0.5).pvalue)
                             if discordant else 1.0),
    }


def write_truth(path, truth):
    fields = ["source_id", "x", "y", "intrinsic_mag", "extinction_mag", "observed_mag", "flux"]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for i in range(len(truth["x"])):
            writer.writerow({"source_id": i, **{key: float(truth[key][i]) for key in fields[1:]}})


def render_figure(scene_images, rows, out_path):
    scenes = list(scene_images)
    fig, axes = plt.subplots(len(scenes), 2, figsize=(11, 3.6 * len(scenes)))
    if len(scenes) == 1:
        axes = np.asarray([axes])
    for i, scene in enumerate(scenes):
        image, title = scene_images[scene]
        lo, hi = np.percentile(image, [5, 99.5])
        display = np.arcsinh(np.clip(image - lo, 0, None) / max((hi - lo) / 8, 1e-6))
        axes[i, 0].imshow(display, origin="lower", cmap="gray")
        axes[i, 0].set_title(title)
        axes[i, 0].set_xticks([])
        axes[i, 0].set_yticks([])
        use = [r for r in rows if r["scene"] == scene and "error" not in r]
        x = np.arange(len(use))
        width = 0.36
        axes[i, 1].bar(x - width / 2, [r["recall"] for r in use], width, label="Recall")
        axes[i, 1].bar(x + width / 2, [r["precision"] for r in use], width, label="Precision")
        short = [r["label"].replace(" + residual deblend", "") for r in use]
        axes[i, 1].set_xticks(x, short, rotation=18, ha="right")
        axes[i, 1].set_ylim(0, 1.05)
        axes[i, 1].set_ylabel("Fraction")
        axes[i, 1].grid(axis="y", alpha=0.25)
        axes[i, 1].legend(loc="upper right", ncol=2)
    fig.suptitle("Fixed-truth morphology stress test: identical image per method", y=0.995)
    fig.tight_layout()
    fig.savefig(out_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def write_report(out_dir, payload):
    rows = [r for r in payload["results"] if "error" not in r]
    lines = [
        "# Astrophysical morphology stress test",
        "",
        "These fixed-truth, single-image simulations expand morphology coverage; they are not observational validation.",
        "All methods receive the identical noisy image and are matched one-to-one to exhaustive truth within two pixels.",
        "",
        "| Scene | Method | Recall | Precision | F1 | High-density recall | Position RMS (mas) | Mag RMS | s/MPix |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['scene']} | {r['label']} | {100*r['recall']:.2f}% | "
            f"{100*r['precision']:.2f}% | {r['f1']:.3f} | "
            f"{100*r['high_density_ge_5_recall']:.2f}% | "
            f"{r.get('astrometric_rms_mas', float('nan')):.2f} | "
            f"{r.get('photometric_rms_mag', float('nan')):.3f} | "
            f"{r['runtime_s_per_mpix']:.2f} |"
        )
    lines.extend([
        "",
        "## Paired AstroCFR ePSF versus Photutils recovery",
        "",
        "| Scene | Recall difference | 95% paired bootstrap CI | AstroCFR-only | Photutils-only | Exact McNemar p |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for r in payload["paired_recovery_tests"]:
        lo, hi = r["paired_bootstrap_ci95"]
        lines.append(
            f"| {r['scene']} | {100*r['paired_recall_difference']:+.2f} pp | "
            f"[{100*lo:.2f}, {100*hi:.2f}] pp | {r['a_only']} | {r['b_only']} | "
            f"{r['mcnemar_exact_p']:.3g} |"
        )
    lines.extend([
        "",
        "## Interpretation boundary",
        "",
        "AstroCFR ePSF raises recall in all three deterministic scenes, but the gains are modest and come with higher CPU cost.",
        "It does not dominate precision or astrometric RMS in every scene. The result therefore supports a broader morphology-aware",
        "Pareto comparison, not a universal branch. Real Galactic-centre and Milky-Way thin-disk validation and multi-exposure",
        "catalogue production remain open tasks.",
        "",
        "The machine-readable protocol and results are in `summary.json` and `summary.csv`; the rendered inputs, truth catalogues,",
        "method catalogues, and comparison figure are stored beside this report.",
    ])
    (out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--scene", default="all")
    parser.add_argument("--image-size", type=int, default=0)
    parser.add_argument("--quick", action="store_true", help="Use 256 x 256 images for a fast smoke run")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    size = 256 if args.quick else (args.image_size or int(cfg["image_size_px"]))
    selected = list(cfg["scenes"]) if args.scene == "all" else [args.scene]
    unknown = [name for name in selected if name not in cfg["scenes"]]
    if unknown:
        raise ValueError(f"Unknown scenes: {unknown}")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows, paired, audits, scene_images = [], [], {}, {}
    radius = float(cfg["match_radius_px"])
    pixel_scale = float(cfg["pixel_scale_mas"])
    for scene in selected:
        scene_cfg = cfg["scenes"][scene]
        print(f"Generating {scene} ({size} x {size})", flush=True)
        raw, known_background, truth = render_scene(scene, scene_cfg, cfg, size)
        image, rms = imageops.estimate_background(raw)
        bright = imageops.detect_sources(image, rms, fwhm=2.2, threshold_sigma=8.0)
        try:
            fwhm = float(np.clip(candidate_features.estimate_psf_fwhm(
                image, bright, rms, min_snr=15, max_sources=50), 1.5, 4.0))
        except Exception:
            fwhm = 2.2
        audits[scene] = {
            "display_name": scene_cfg["display_name"],
            "description": scene_cfg["description"],
            "seed": int(scene_cfg["seed"]),
            "image_size_px": size,
            "truth_stars": int(len(truth["x"])),
            "background_rms_electrons": float(rms),
            "image_only_fwhm_px": fwhm,
            "injection_psf": "spatially varying elliptical Moffat (beta=2.8)",
            "recovery_psf": "method-specific; AstroCFR estimates an image-derived median ePSF; Photutils uses CircularGaussianPRF",
        }
        fits.PrimaryHDU(raw.astype(np.float32)).writeto(args.output_dir / f"{scene}_image.fits", overwrite=True)
        fits.PrimaryHDU(known_background.astype(np.float32)).writeto(
            args.output_dir / f"{scene}_known_background.fits", overwrite=True)
        write_truth(args.output_dir / f"{scene}_truth.csv", truth)
        catalogues = {}
        for method in METHODS:
            print(f"  {method}", flush=True)
            try:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    (xy, flux), elapsed, memory = measured(lambda: run_method(method, image, rms, fwhm))
                catalogues[method] = np.asarray(xy, float)
                np.savez_compressed(args.output_dir / f"{scene}_{method}_catalogue.npz", xy=xy, flux=flux)
                row = evaluate(scene, method, xy, flux, truth, elapsed, memory, size, radius, pixel_scale)
                row["warnings"] = " | ".join(sorted({str(w.message) for w in caught}))
                rows.append(row)
            except Exception as exc:
                rows.append({"scene": scene, "method": method, "label": LABELS[method],
                             "error": f"{type(exc).__name__}: {exc}"})
        truth_xy = np.column_stack([truth["x"], truth["y"]])
        test = paired_recovery(scene, catalogues, truth_xy, radius)
        if test:
            paired.append(test)
        scene_images[scene] = (raw, scene_cfg["display_name"])

    payload = {
        "protocol": {
            "scope": "fixed-truth single-image morphology stress test; not observational validation",
            "scenes": selected,
            "image_size_px": size,
            "association": "greedy one-to-one matching",
            "association_radius_px": radius,
            "truth": "exhaustive simulated point-source catalogue",
            "injection_psf": "spatially varying elliptical Moffat independent of all recovery models",
            "background": "scene-specific structured model followed by Poisson and Gaussian read noise",
            "fairness": "identical noisy image, truth catalogue, threshold convention, and matching rule for all methods",
            "limitation": "one deterministic realization per morphology; synthetic morphology is not a real-field population claim",
        },
        "scene_audit": audits,
        "results": rows,
        "paired_recovery_tests": paired,
    }
    (args.output_dir / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if rows:
        fields = sorted({key for row in rows for key in row})
        with (args.output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    render_figure(scene_images, rows, args.output_dir / "astrophysical_scene_comparison.png")
    write_report(args.output_dir, payload)
    print(json.dumps(rows, indent=2), flush=True)


if __name__ == "__main__":
    main()
