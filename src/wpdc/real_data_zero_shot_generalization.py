#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Zero-shot simulated-to-real generalization experiment for WPDC.

This script deliberately stops before every label-dependent WPDC operation
(ML threshold fitting, forced recovery, astrometric and photometric fitting).
It trains the existing handcrafted-feature RandomForest on CSST simulations
only, locks its threshold on a simulation-only validation split, and evaluates
the frozen detector/filter on public survey images.  Gaia DR3 is used only for
the final external evaluation, never for real-image tuning.

Public data downloaded automatically:
  * Pan-STARRS1 stacked i-band cutout centred on M31;
  * DESI Legacy Survey DR10 r-band cutout centred on M13;
  * Gaia DR3 catalogue rows in the two cutout footprints (VizieR).

Run from this directory:
  python real_data_zero_shot_generalization.py
"""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import matplotlib.pyplot as plt
import numpy as np
import requests
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from photutils.background import Background2D, MMMBackground
from photutils.detection import DAOStarFinder
from scipy.spatial import cKDTree
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA_DIR = HERE / "real_data"
OUT_DIR = HERE / "real_data_generalization_results"
PIPELINE = HERE / "candidate_features.py"
CHIPS = (12, 13, 17, 18)
SIM_PREFIX = "CSST_MSC_MS_WIDE_20280101000000_20280101000230_10100300001"
RNG = np.random.default_rng(20260806)

# 1200 pixels gives a reproducible 5-arcmin field, while remaining practical
# for repeated source extraction on a workstation.
DOMAINS = (
    {"name": "PS1_M31_i", "survey": "Pan-STARRS1", "ra": 10.6847,
     "dec": 41.2687, "band": "i", "size": 1200, "pixscale": 0.25},
    {"name": "LS_DR10_M13_r", "survey": "DESI Legacy Survey DR10",
     "ra": 250.4230, "dec": 36.4610, "band": "r", "size": 1200,
     "pixscale": 0.262},
)


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("wpdc_real_transfer", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def download(url: str, destination: Path) -> None:
    if destination.exists() and destination.stat().st_size > 1000:
        return
    response = requests.get(url, timeout=180)
    response.raise_for_status()
    destination.write_bytes(response.content)


def download_ps1(domain: dict) -> Path:
    out = DATA_DIR / f"{domain['name']}.fits"
    if out.exists() and out.stat().st_size > 1000:
        return out
    q = ("https://ps1images.stsci.edu/cgi-bin/ps1filenames.py?ra="
         f"{domain['ra']}&dec={domain['dec']}&size={domain['size']}&filters={domain['band']}")
    text = requests.get(q, timeout=120).text.strip().splitlines()
    if len(text) < 2:
        raise RuntimeError(f"PS1 filename query returned no image: {text}")
    filename = text[1].split()[-3]
    url = ("https://ps1images.stsci.edu/cgi-bin/fitscut.cgi?ra="
           f"{domain['ra']}&dec={domain['dec']}&size={domain['size']}&format=fits"
           f"&red={quote(filename, safe='/')}")
    download(url, out)
    return out


def download_legacy(domain: dict) -> Path:
    out = DATA_DIR / f"{domain['name']}.fits"
    url = ("https://www.legacysurvey.org/viewer/fits-cutout?ra="
           f"{domain['ra']}&dec={domain['dec']}&layer=ls-dr10&pixscale={domain['pixscale']}"
           f"&size={domain['size']}&bands={domain['band']}")
    download(url, out)
    return out


def download_gaia(domain: dict, image_path: Path) -> Path:
    out = DATA_DIR / f"{domain['name']}_gaia_dr3.tsv"
    if out.exists() and out.stat().st_size > 100:
        return out
    with fits.open(image_path) as hdul:
        header = next(h.header for h in hdul if h.data is not None)
    wcs = WCS(header)
    scale = float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)
    radius = max(domain["size"] * scale / 3600.0 * 0.76, 0.08)
    url = ("https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=I/355/gaiadr3"
           f"&-c={domain['ra']}+{domain['dec']}&-c.r={radius}&-c.u=deg"
           "&-out=Source,RA_ICRS,DE_ICRS,Gmag&-out.max=50000")
    download(url, out)
    return out


def read_image(path: Path):
    with fits.open(path) as hdul:
        hdu = next(h for h in hdul if h.data is not None)
        image = np.asarray(hdu.data, dtype=float).squeeze()
        header = hdu.header.copy()
    if image.ndim != 2:
        raise ValueError(f"Expected a 2-D image; got {image.shape} from {path}")
    finite = np.isfinite(image)
    fill = np.nanmedian(image[finite])
    image = np.where(finite, image, fill)
    return image, header


def read_gaia(tsv_path: Path, header, g_limit: float = 20.0) -> Table:
    lines = [line for line in tsv_path.read_text(encoding="utf-8", errors="replace").splitlines()
             if line and not line.startswith("#") and not line.startswith("-")]
    # VizieR TSV contains a units row after the header.  Parse the four requested
    # columns explicitly, avoiding version-dependent astropy ASCII guessing.
    rows = [line.split() for line in lines[2:] if len(line.split()) >= 4]
    source_id = np.asarray([row[0] for row in rows])
    ra = np.asarray([row[1] for row in rows], dtype=float)
    dec = np.asarray([row[2] for row in rows], dtype=float)
    gmag = np.asarray([row[3] for row in rows], dtype=float)
    wcs = WCS(header)
    x, y = wcs.world_to_pixel_values(ra, dec)
    ny, nx = wcs.array_shape or (0, 0)
    if nx == 0:
        nx, ny = 1200, 1200
    margin = 12
    good = (gmag <= g_limit) & np.isfinite(x) & np.isfinite(y) & (x >= margin) & (x < nx-margin) & (y >= margin) & (y < ny-margin)
    out = Table()
    out["source_id"] = source_id[good]
    out["ra"] = ra[good]; out["dec"] = dec[good]; out["gmag"] = gmag[good]
    out["x"] = x[good]; out["y"] = y[good]
    return out


def estimate_background(image: np.ndarray):
    # Modest boxes preserve the galaxy/cluster-scale background without using
    # any catalogue information.
    bkg = Background2D(image, box_size=(64, 64), filter_size=(3, 3),
                       bkg_estimator=MMMBackground(), exclude_percentile=20)
    return image - bkg.background, float(bkg.background_rms_median)


def detect_sources(image_sub: np.ndarray, bkg_rms: float, fwhm: float = 3.5,
                   threshold_sigma: float = 4.0):
    finder = DAOStarFinder(fwhm=fwhm, threshold=threshold_sigma * bkg_rms,
                           sharpness_range=(0.05, 2.0), roundness_range=(-1.0, 1.0),
                           exclude_border=True)
    sources = finder(image_sub)
    if sources is None:
        return Table()
    return sources


def greedy_match(det_xy: np.ndarray, ref_xy: np.ndarray, radius_px: float):
    if len(det_xy) == 0 or len(ref_xy) == 0:
        return np.zeros(len(det_xy), dtype=bool), np.full(len(det_xy), -1, dtype=int)
    tree = cKDTree(ref_xy)
    d, idx = tree.query(det_xy, k=1)
    pairs = sorted((float(dd), int(i), int(j)) for i, (dd, j) in enumerate(zip(d, idx)) if dd <= radius_px)
    matched = np.zeros(len(det_xy), dtype=bool); ridx = np.full(len(det_xy), -1, dtype=int); used = set()
    for _, i, j in pairs:
        if j not in used:
            matched[i] = True; ridx[i] = j; used.add(j)
    return matched, ridx


def source_features(mod, sources: Table, image_sub: np.ndarray, bkg_rms: float):
    xcol, ycol = mod._xy_columns(sources)
    xy = np.column_stack([np.asarray(sources[xcol], float), np.asarray(sources[ycol], float)])
    x, _ = mod._extract_clf_features(sources, image_sub, bkg_rms)
    return x, xy


def simulation_training_set(mod):
    """Build a compact, high-SNR simulation-only training set.

    This reuses WPDC's detector and 17 handcrafted features but avoids the
    label-dependent later pipeline stages.  Candidates within 2 px are positive;
    2--8 px are excluded as ambiguous, and distant candidates are negatives.
    """
    cache = OUT_DIR / "simulation_training_features.npz"
    if cache.exists():
        loaded = np.load(cache, allow_pickle=False)
        print(f"Loaded cached simulation training features: {cache}")
        return loaded["X"], loaded["y"], loaded["groups"], {"cache_reused": True}
    Xs, ys, groups = [], [], []
    timing = {}
    for chip in CHIPS:
        t0 = time.perf_counter()
        image, _ = read_image(ROOT / f"{SIM_PREFIX}_{chip}_L0_V01.fits")
        ref = Table.read(ROOT / f"{SIM_PREFIX}_{chip}_L0_V01_top1000.cat", format="ascii",
                         names=['obj_ID','ID_chip','filter','xImage','yImage','ra','dec','ra_orig','dec_orig','z','mag','obj_type',
                                'pm_ra','pm_dec','RV','parallax','av','stellarmass','dm','teff','logg','feh','bulgemass','diskmass','detA','e1','e2','kappa','g1','g2','size','galType','veldisp'])
        sub, rms = estimate_background(image)
        sources = detect_sources(sub, rms, fwhm=3.0, threshold_sigma=6.0)
        if len(sources) == 0:
            raise RuntimeError(f"No training candidates detected on simulation chip {chip}")
        feats, xy = source_features(mod, sources, sub, rms)
        dist, _ = cKDTree(np.column_stack([ref['xImage'], ref['yImage']])).query(xy, k=1)
        pos = np.where(dist < 2.0)[0]
        neg = np.where(dist > 8.0)[0]
        # Limit the easy negative majority while retaining spatial diversity.
        nneg = min(len(neg), max(len(pos) * 5, 400), 2000)
        if len(neg) > nneg:
            neg = RNG.choice(neg, nneg, replace=False)
        idx = np.concatenate([pos, neg])
        Xs.append(feats[idx]); ys.append(np.r_[np.ones(len(pos)), np.zeros(len(neg))]); groups.extend([chip] * len(idx))
        timing[f"sim_chip_{chip}_s"] = time.perf_counter() - t0
        print(f"Simulation chip {chip}: candidates={len(sources)}, train pos={len(pos)}, neg={len(neg)}")
    X = np.vstack(Xs); y = np.concatenate(ys).astype(int); groups = np.asarray(groups)
    np.savez_compressed(cache, X=X, y=y, groups=groups)
    return X, y, groups, timing


def fit_frozen_classifier(X, y, groups):
    # Every chip contributes to both train/validation; this threshold is still
    # simulation-only.  Per-chip z-score uses only unlabeled feature moments.
    Xnorm = X.copy()
    for chip in np.unique(groups):
        mask = groups == chip
        Xnorm[mask] = (X[mask] - X[mask].mean(axis=0)) / np.maximum(X[mask].std(axis=0), 1e-8)
    train_idx, val_idx = train_test_split(np.arange(len(y)), test_size=0.25, random_state=20260806,
                                          stratify=y)
    clf = RandomForestClassifier(n_estimators=400, max_depth=15, min_samples_leaf=2,
                                 max_features="sqrt", class_weight={0: 1, 1: 6},
                                 random_state=20260806, n_jobs=-1)
    clf.fit(Xnorm[train_idx], y[train_idx])
    pv = clf.predict_proba(Xnorm[val_idx])[:, 1]
    # The highest threshold retaining >=90% recall on simulation validation.
    thresholds = np.unique(pv)
    feasible = []
    for thr in thresholds:
        pred = pv >= thr
        rec = np.sum(pred & (y[val_idx] == 1)) / max(np.sum(y[val_idx] == 1), 1)
        prec = np.sum(pred & (y[val_idx] == 1)) / max(np.sum(pred), 1)
        if rec >= 0.90:
            feasible.append((prec, thr, rec))
    if not feasible:
        threshold = 0.5
    else:
        _, threshold, _ = max(feasible)
    pred = pv >= threshold
    val_metrics = {"threshold": float(threshold),
                   "recall": float(np.sum(pred & (y[val_idx] == 1)) / max(np.sum(y[val_idx] == 1), 1)),
                   "precision": float(np.sum(pred & (y[val_idx] == 1)) / max(np.sum(pred), 1)),
                   "n_train": int(len(train_idx)), "n_validation": int(len(val_idx))}
    # Refit on all simulation candidates after the frozen threshold is selected.
    clf.fit(Xnorm, y)
    return clf, threshold, val_metrics


def evaluate_domain(mod, domain, clf, threshold):
    image_path = download_ps1(domain) if domain["survey"] == "Pan-STARRS1" else download_legacy(domain)
    gaia_path = download_gaia(domain, image_path)
    timings = {}; t0 = time.perf_counter()
    image, header = read_image(image_path)
    timings["load_s"] = time.perf_counter() - t0
    t0 = time.perf_counter(); sub, rms = estimate_background(image); timings["background_s"] = time.perf_counter() - t0
    t0 = time.perf_counter(); sources = detect_sources(sub, rms, fwhm=3.5, threshold_sigma=4.0); timings["detection_s"] = time.perf_counter() - t0
    if len(sources) == 0:
        raise RuntimeError(f"No candidates in {domain['name']}")
    t0 = time.perf_counter(); X, xy = source_features(mod, sources, sub, rms); timings["features_s"] = time.perf_counter() - t0
    # Unlabeled target-domain normalization is allowed at inference; it does not
    # access Gaia and mirrors the per-chip normalization used in WPDC's LOCO code.
    Xnorm = (X - X.mean(axis=0)) / np.maximum(X.std(axis=0), 1e-8)
    t0 = time.perf_counter(); proba = clf.predict_proba(Xnorm)[:, 1]; keep = proba >= threshold; timings["inference_s"] = time.perf_counter() - t0
    gaia = read_gaia(gaia_path, header, g_limit=20.0)
    scale = float(np.mean(proj_plane_pixel_scales(WCS(header)))*3600.0)
    radius_px = 0.75 / scale  # fixed 0.75 arcsec celestial association radius
    raw_match, _ = greedy_match(xy, np.column_stack([gaia['x'], gaia['y']]), radius_px)
    filt_match, _ = greedy_match(xy[keep], np.column_stack([gaia['x'], gaia['y']]), radius_px)
    nref = len(gaia)
    summary = {"domain": domain["name"], "survey": domain["survey"], "band": domain["band"],
               "image": str(image_path), "gaia_reference_g_le_20": int(nref), "candidates": int(len(sources)),
               "retained": int(np.sum(keep)), "threshold": float(threshold), "pixel_scale_arcsec": scale,
               "match_radius_arcsec": 0.75, "raw_matched_gaia": int(raw_match.sum()),
               "retained_matched_gaia": int(filt_match.sum()),
               "raw_gaia_recall": float(raw_match.sum()/max(nref, 1)),
               "retained_gaia_recall": float(filt_match.sum()/max(nref, 1)),
               "raw_gaia_match_rate": float(raw_match.sum()/max(len(sources), 1)),
               "retained_gaia_match_rate": float(filt_match.sum()/max(np.sum(keep), 1)),
               "background_rms": float(rms), **timings}
    DATA_DIR.mkdir(exist_ok=True); OUT_DIR.mkdir(exist_ok=True)
    cat = Table()
    cat["x"] = xy[:,0]; cat["y"] = xy[:,1]; cat["probability"] = proba; cat["retained"] = keep; cat["gaia_matched_raw"] = raw_match
    cat.write(OUT_DIR / f"{domain['name']}_candidates.ecsv", format="ascii.ecsv", overwrite=True)
    gaia.write(OUT_DIR / f"{domain['name']}_gaia_reference.ecsv", format="ascii.ecsv", overwrite=True)
    return summary, image, xy, keep, gaia


def save_figure(domain_results):
    fig, axes = plt.subplots(1, len(domain_results), figsize=(6*len(domain_results), 5), constrained_layout=True)
    axes = np.atleast_1d(axes)
    for ax, (summary, image, xy, keep, gaia) in zip(axes, domain_results):
        lo, hi = np.nanpercentile(image, [5, 99.5])
        ax.imshow(image, origin="lower", cmap="gray", vmin=lo, vmax=hi)
        ax.scatter(gaia['x'], gaia['y'], s=12, facecolors="none", edgecolors="#35c6ff", linewidths=0.6, label="Gaia DR3 G≤20")
        ax.scatter(xy[keep,0], xy[keep,1], s=5, c="#ffbf2f", alpha=0.7, label="frozen WPDC filter")
        ax.set_title(f"{summary['survey']}\n{summary['domain']}")
        ax.set_xlabel("x (pixel)"); ax.set_ylabel("y (pixel)")
        ax.legend(loc="upper right", fontsize=7)
    fig.savefig(OUT_DIR / "zero_shot_real_domains.png", dpi=220)
    plt.close(fig)


def write_report(val, results, train_timing):
    lines = ["# WPDC simulated-to-real zero-shot generalization", "",
             "Protocol: RandomForest uses WPDC's existing 17 handcrafted features. It is trained only on CSST-like simulations (chips 12, 13, 17, 18). The decision threshold is selected only on a held-out simulation split. No real-image Gaia labels are used for training, thresholding, forced recovery, calibration, or model selection.", "",
             f"Simulation-only validation: n_train={val['n_train']}, n_validation={val['n_validation']}, threshold={val['threshold']:.4f}, recall={val['recall']:.3f}, precision={val['precision']:.3f}.", "",
             "## External-domain results", "",
             "`Gaia recall` is the one-to-one recovery rate for Gaia DR3 G<=20 references inside the image. `Gaia match rate` is reported as a catalogue-coverage lower bound, not as true precision: real survey images contain genuine objects below Gaia/completeness and unresolved blends.", "",
             "| Domain | Gaia refs | Candidates | Retained | Raw Gaia recall | Retained Gaia recall | Retained Gaia match-rate lower bound | Runtime (s) |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        runtime = r['load_s'] + r['background_s'] + r['detection_s'] + r['features_s'] + r['inference_s']
        lines.append(f"| {r['domain']} ({r['survey']}, {r['band']}) | {r['gaia_reference_g_le_20']} | {r['candidates']} | {r['retained']} | {r['raw_gaia_recall']:.3f} | {r['retained_gaia_recall']:.3f} | {r['retained_gaia_match_rate']:.3f} | {runtime:.2f} |")
    lines += ["", "## Runtime breakdown", "", "| Domain | Load | Background | Detection | Feature extraction | Inference |", "|---|---:|---:|---:|---:|---:|"]
    for r in results:
        lines.append(f"| {r['domain']} | {r['load_s']:.2f} | {r['background_s']:.2f} | {r['detection_s']:.2f} | {r['features_s']:.2f} | {r['inference_s']:.2f} |")
    lines += ["", "Simulation feature-set construction time (per chip, seconds):", json.dumps(train_timing, indent=2), "",
              "Caveats: This is a classifier/filter transfer test, not a full end-to-end photometric calibration claim. The original full WPDC path contains reference-dependent forced photometry and calibration, which are intentionally disabled here to prevent external-label leakage. Pan-STARRS1 M31 is strongly crowded; Gaia itself is incomplete there, so its match rate must not be labelled catalogue purity."]
    (OUT_DIR / "real_data_generalization_report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    DATA_DIR.mkdir(exist_ok=True); OUT_DIR.mkdir(exist_ok=True)
    mod = load_module(PIPELINE)
    t0 = time.perf_counter(); X, y, groups, train_timing = simulation_training_set(mod); train_timing['total_s'] = time.perf_counter()-t0
    clf, threshold, val = fit_frozen_classifier(X, y, groups)
    summaries, plot_data = [], []
    for domain in DOMAINS:
        print(f"\nExternal zero-shot evaluation: {domain['name']}")
        summary, image, xy, keep, gaia = evaluate_domain(mod, domain, clf, threshold)
        print(json.dumps(summary, indent=2))
        summaries.append(summary); plot_data.append((summary, image, xy, keep, gaia))
    with (OUT_DIR / "real_data_generalization_summary.json").open("w", encoding="utf-8") as f:
        json.dump({"simulation_validation": val, "results": summaries, "training_timing": train_timing}, f, indent=2)
    with (OUT_DIR / "real_data_generalization_summary.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summaries[0]))
        writer.writeheader(); writer.writerows(summaries)
    save_figure(plot_data); write_report(val, summaries, train_timing)
    print(f"\nCompleted. Results: {OUT_DIR}")


if __name__ == "__main__":
    main()
