#!/usr/bin/env python
"""Blind, leave-one-chip-out CSST-like candidate-catalogue audit.

No test-chip reference catalogue is consulted for detection, feature scaling,
classification, threshold selection, quality filtering, or injected-source
recovery.  Its catalogue is opened only after the final candidate catalogue is
written, to score one-to-one catalogue-scoped matches.  The supplied challenge
``top1000`` lists may not represent every image object, so the precision-like
quantity is deliberately named a *catalogue-match fraction*, not blind purity
or an astrophysical false-discovery rate.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.table import Table
from scipy.spatial import cKDTree
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for item in (REPO / "src" / "wpdc",):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))
import candidate_features as feat  # noqa: E402
import real_data_zero_shot_generalization as base  # noqa: E402

CHIPS = (12, 13, 17, 18)
PREFIX = "CSST_MSC_MS_WIDE_20280101000000_20280101000230_10100300001"
RNG = np.random.default_rng(20260811)
CAT_NAMES = ['obj_ID','ID_chip','filter','xImage','yImage','ra','dec','ra_orig','dec_orig','z','mag','obj_type',
             'pm_ra','pm_dec','RV','parallax','av','stellarmass','dm','teff','logg','feh','bulgemass','diskmass',
             'detA','e1','e2','kappa','g1','g2','size','galType','veldisp']


def resolve_data(explicit: Path | None) -> Path:
    if explicit:
        return explicit
    matches = []
    for candidate in REPO.parent.glob(f"CSST_*/**/{PREFIX}_12_L0_V01.fits"):
        parent = candidate.parent
        if all((parent / f"{PREFIX}_{chip}_L0_V01.fits").exists() for chip in CHIPS):
            matches.append(parent)
    # Prefer the original registered data directory rather than duplicated
    # intermediate result copies.
    matches = sorted(set(matches), key=lambda p: ("fanhuaxing" in str(p).lower(), len(str(p))))
    if not matches:
        raise FileNotFoundError("Provide --simulation-dir containing the four CSST-like FITS chips")
    return matches[0]


def image(path: Path, crop_side: int | None = 3000):
    with fits.open(path, memmap=False) as hdul:
        hdu = next(h for h in hdul if h.data is not None)
        ny, nx = hdu.data.shape[-2:]
        if crop_side is None or crop_side >= min(nx, ny):
            x0 = y0 = 0
            data = np.asarray(hdu.data, float).squeeze()
        else:
            x0, y0 = (nx - crop_side) // 2, (ny - crop_side) // 2
            data = np.asarray(hdu.data[y0:y0+crop_side, x0:x0+crop_side], float).squeeze()
    finite = np.isfinite(data)
    return np.where(finite, data, np.nanmedian(data[finite])), np.array([x0, y0], float)


def reference(path: Path, offset=(0.0, 0.0), shape=None):
    tab = Table.read(path, format="ascii", names=CAT_NAMES)
    xy = np.column_stack([np.asarray(tab['xImage'], float), np.asarray(tab['yImage'], float)]) - np.asarray(offset, float)
    if shape is not None:
        keep = (xy[:, 0] >= 0) & (xy[:, 0] < shape[1]) & (xy[:, 1] >= 0) & (xy[:, 1] < shape[0])
        xy = xy[keep]
    return xy


def candidates(data, need_features=True):
    sub, rms = base.estimate_background(data)
    src = base.detect_sources(sub, rms, fwhm=3.0, threshold_sigma=4.0)
    if len(src) == 0:
        return sub, rms, np.empty((0, 17)), np.empty((0, 2))
    X = feat._extract_clf_features(src, sub, rms)[0] if need_features else None
    xcol, ycol = feat._xy_columns(src)
    xy = np.column_stack([np.asarray(src[xcol], float), np.asarray(src[ycol], float)])
    return sub, rms, X, xy


def labels_for_training(xy, refxy):
    distances, _ = cKDTree(refxy).query(xy, k=1)
    keep = (distances < 2.0) | (distances > 8.0)
    return keep, (distances[keep] < 2.0).astype(int)


def normalize(X):
    return (X - X.mean(axis=0)) / np.maximum(X.std(axis=0), 1e-8)


def threshold_from_validation(y, probability):
    feasible = []
    for threshold in np.unique(probability):
        selected = probability >= threshold
        recall = np.sum(selected & (y == 1)) / max(np.sum(y == 1), 1)
        match_fraction = np.sum(selected & (y == 1)) / max(np.sum(selected), 1)
        if recall >= .90:
            feasible.append((match_fraction, threshold))
    return float(max(feasible)[1] if feasible else .5)


def one_to_one(det, refxy, radius=2.0):
    if not len(det) or not len(refxy):
        return np.zeros(len(det), bool), np.zeros(len(refxy), bool)
    d, idx = cKDTree(refxy).query(det, k=1)
    pairs = sorted((dd, i, j) for i, (dd, j) in enumerate(zip(d, idx)) if dd <= radius)
    dm = np.zeros(len(det), bool); rm = np.zeros(len(refxy), bool)
    for _, i, j in pairs:
        if not dm[i] and not rm[j]:
            dm[i] = True; rm[j] = True
    return dm, rm


def inject_gaussians(data, xy, peak, fwhm=3.0):
    output = data.copy(); sigma = fwhm / 2.35482; radius = int(np.ceil(4 * sigma))
    yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1]
    for x, y in xy:
        ix, iy = int(round(x)), int(round(y))
        x0, x1 = max(0, ix-radius), min(data.shape[1], ix+radius+1)
        y0, y1 = max(0, iy-radius), min(data.shape[0], iy+radius+1)
        kernel = peak * np.exp(-((xx + ix - x) ** 2 + (yy + iy - y) ** 2) / (2*sigma*sigma))
        output[y0:y1, x0:x1] += kernel[(y0-iy+radius):(y1-iy+radius), (x0-ix+radius):(x1-ix+radius)]
    return output


def injection_sites(raw_xy, shape, density_band, count=40):
    tree = cKDTree(raw_xy) if len(raw_xy) else None
    accepted = []
    for _ in range(100000):
        x = RNG.uniform(15, shape[1]-15); y = RNG.uniform(15, shape[0]-15)
        nearby = len(tree.query_ball_point([x, y], 10)) if tree else 0
        nearest = tree.query([x, y], k=1)[0] if tree else np.inf
        target = (nearby <= 1) if density_band == "low" else (nearby >= 3)
        if target and nearest > 3 and all(np.hypot(x-a, y-b) >= 20 for a, b in accepted):
            accepted.append((x, y))
            if len(accepted) == count:
                break
    return np.asarray(accepted, float)


def wilson(k, n, z=1.96):
    if not n:
        return [None, None]
    p = k/n; den = 1+z*z/n
    mid = (p+z*z/(2*n))/den
    half = z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [float(max(0, mid-half)), float(min(1, mid+half))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulation-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, default=REPO / "results" / "csst_blind_loco_audit")
    parser.add_argument("--skip-injections", action="store_true", help="write the closed-book catalogue audit only")
    parser.add_argument("--injections-per-stratum", type=int, default=16)
    parser.add_argument("--crop-side", type=int, default=3000,
                        help="pre-registered central square; 0 uses each full chip")
    parser.add_argument("--branch", choices=("rf_loco", "image_only_proposal"), default="rf_loco",
                        help="rf_loco is a strict leave-one-chip-out RF screen; image_only_proposal is its label-free frontend")
    args = parser.parse_args(); data_dir = resolve_data(args.simulation_dir); args.output_dir.mkdir(parents=True, exist_ok=True)
    cache = {}
    for chip in CHIPS:
        raw, offset = image(data_dir / f"{PREFIX}_{chip}_L0_V01.fits", args.crop_side or None)
        sub, rms, X, xy = candidates(raw, need_features=args.branch == "rf_loco")
        cache[chip] = {"raw": raw, "sub": sub, "rms": rms, "X": X, "xy": xy,
                       "ref_path": data_dir / f"{PREFIX}_{chip}_L0_V01_top1000.cat", "offset": offset}
    catalogue_rows, injection_rows = [], []
    for test_chip in CHIPS:
        test = cache[test_chip]
        if args.branch == "rf_loco":
            train_X, train_y = [], []
            for chip in CHIPS:
                if chip == test_chip:
                    continue
                keep, y = labels_for_training(cache[chip]["xy"], reference(cache[chip]["ref_path"], cache[chip]["offset"], cache[chip]["raw"].shape))
                train_X.append(normalize(cache[chip]["X"])[keep]); train_y.append(y)
            train_X, train_y = np.vstack(train_X), np.concatenate(train_y)
            tr, val = train_test_split(np.arange(len(train_y)), test_size=.25, random_state=20260811+test_chip, stratify=train_y)
            clf = RandomForestClassifier(n_estimators=200, max_depth=15, min_samples_leaf=2, max_features="sqrt",
                                         class_weight={0: 1, 1: 6}, random_state=20260811+test_chip, n_jobs=6)
            clf.fit(train_X[tr], train_y[tr]); threshold = threshold_from_validation(train_y[val], clf.predict_proba(train_X[val])[:, 1])
            clf.fit(train_X, train_y)
            probability = clf.predict_proba(normalize(test["X"]))[:, 1]
            selected = probability >= threshold
        else:
            clf = None; threshold = None
            probability = np.full(len(test["xy"]), np.nan)
            selected = np.ones(len(test["xy"]), dtype=bool)
        # Persist the blind output before opening the withheld-chip catalogue.
        blind_catalogue = Table({"x": test["xy"][selected, 0], "y": test["xy"][selected, 1],
                                "classifier_probability": probability[selected]})
        blind_catalogue.meta["truth_catalogue_used"] = "no"
        blind_catalogue.meta["threshold"] = threshold
        blind_catalogue.write(args.output_dir / f"chip{test_chip}_blind_catalogue.ecsv", format="ascii.ecsv", overwrite=True)
        # This is the first and only access to the withheld chip catalogue.
        ref = reference(test["ref_path"], test["offset"], test["raw"].shape)
        dm, rm = one_to_one(test["xy"][selected], ref)
        catalogue_rows.append({"test_chip": test_chip, "train_chips": ";".join(map(str, [x for x in CHIPS if x != test_chip])),
                               "threshold": threshold, "raw_candidates": len(test["xy"]), "retained_candidates": int(selected.sum()),
                               "references": len(ref), "matched": int(dm.sum()),
                               "catalogue_scoped_completeness": None if not len(rm) else float(rm.mean()),
                               "catalogue_match_fraction": float(dm.mean()) if len(dm) else 0.0,
                               "protocol": "test reference withheld until final one-to-one scoring"})
        if args.skip_injections:
            continue
        for band in ("low", "high"):
            sites = injection_sites(test["xy"], test["raw"].shape, band, count=args.injections_per_stratum)
            for peak_snr in (10, 30):
                if not len(sites):
                    injection_rows.append({"test_chip": test_chip, "density_band": band, "injected_peak_snr": peak_snr,
                                           "injected": 0, "recovered": 0, "recovery": None, "ci95": [None, None]}); continue
                injected = inject_gaussians(test["raw"], sites, peak=peak_snr*test["rms"])
                _, irms, iX, ixy = candidates(injected, need_features=args.branch == "rf_loco")
                if clf is None:
                    keep = np.ones(len(ixy), dtype=bool)
                else:
                    ip = clf.predict_proba(normalize(iX))[:, 1]; keep = ip >= threshold
                _, matched_injected = one_to_one(ixy[keep], sites)
                k, n = int(matched_injected.sum()), int(len(sites))
                injection_rows.append({"test_chip": test_chip, "density_band": band, "injected_peak_snr": peak_snr,
                                       "injected": n, "recovered": k, "recovery": k/n, "ci95": wilson(k, n),
                                       "background_rms_after_injection": float(irms),
                                       "protocol": "injection positions are withheld from candidate generation and classification"})
    payload = {"protocol": {"leave_one_chip_out": True, "training_truth": "other three simulation chips only",
                             "test_truth": "opened only after final candidate catalogue is written", "association_radius_px": 2.0,
                             "precision_name": "catalogue-match fraction; not blind astrophysical purity",
                             "injections": "image-only local-density strata; injected positions withheld from pipeline",
                             "central_crop_side_px": args.crop_side or "full chip", "branch": args.branch},
               "catalogue_results": catalogue_rows, "injection_results": injection_rows}
    (args.output_dir / "blind_loco_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for name, records in (("blind_loco_catalogue.csv", catalogue_rows), ("blind_loco_injections.csv", injection_rows)):
        if not records:
            continue
        with (args.output_dir / name).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=records[0].keys()); w.writeheader(); w.writerows(records)
    lines = ["# Blind CSST-like leave-one-chip-out audit", "",
             "The withheld-chip catalogue was not loaded by detection, scaling, classification, threshold selection, or injection recovery. It was read only for final scoring. `catalogue_match_fraction` is deliberately not called blind purity because the supplied top1000 catalogue may be incomplete.", "",
             "| Test chip | Retained | References | Matched | Catalogue-scoped completeness | Catalogue-match fraction |", "|---:|---:|---:|---:|---:|---:|"]
    for row in catalogue_rows:
        comp = "NA" if row["catalogue_scoped_completeness"] is None else f"{row['catalogue_scoped_completeness']:.3f}"
        lines.append(f"| {row['test_chip']} | {row['retained_candidates']} | {row['references']} | {row['matched']} | {comp} | {row['catalogue_match_fraction']:.3f} |")
    if injection_rows:
        lines += ["", "## Blind artificial-source recovery", "", "Injected positions are image-only density strata and are not passed to the pipeline.", "",
                  "| Chip | Density | Peak SNR | Injected | Recovered | Recovery (Wilson 95%) |", "|---:|---|---:|---:|---:|---|"]
        for row in injection_rows:
            ci = row["ci95"]; ci_text = "NA" if ci[0] is None else f"{row['recovery']:.3f} [{ci[0]:.3f}, {ci[1]:.3f}]"
            lines.append(f"| {row['test_chip']} | {row['density_band']} | {row['injected_peak_snr']} | {row['injected']} | {row['recovered']} | {ci_text} |")
    (args.output_dir / "README.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
