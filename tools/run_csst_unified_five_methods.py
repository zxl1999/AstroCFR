#!/usr/bin/env python
"""Run a common image-only five-branch benchmark on the four CSST chips.

The registered CSST full-frame audit is retained separately.  This benchmark
adds a reproducible 1200x1200 crop tier so DAO, SEP, Photutils, ePSF-deblend,
and spatial-ePSF joint fitting share one candidate/measurement protocol.  The
reference catalogue is read only after image-only candidate generation and
fitting, for spatial registration, zero-point estimation, and scoring.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
import sep
from astropy.io import fits
from astropy.table import Table
from photutils.detection import DAOStarFinder
from photutils.psf import CircularGaussianPRF, PSFPhotometry, SourceGrouper
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
CSST_ROOT = next(p for p in ROOT.parent.iterdir() if p.is_dir() and p.name.startswith("CSST_"))
CSST_DATA = next(p for p in CSST_ROOT.iterdir() if p.is_dir() and any("top1000.cat" in x.name for x in p.glob("*.cat")))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(CSST_DATA / "fanhuaxing"))
from wpdc import hst_epsf_deblend_artificial_stars as epsf  # noqa: E402
from wpdc import hst_spatial_epsf_joint_pilot as spatial  # noqa: E402

CHIPS = (12, 13, 17, 18)
SIZE = 1200
PIXEL_SCALE_MAS = 74.0
CAT_NAMES = [
    "obj_ID", "ID_chip", "filter", "xImage", "yImage", "ra", "dec",
    "ra_orig", "dec_orig", "z", "mag", "obj_type", "pm_ra", "pm_dec",
    "RV", "parallax", "av", "stellarmass", "dm", "teff", "logg", "feh",
    "bulgemass", "diskmass", "detA", "e1", "e2", "kappa", "g1", "g2",
    "size", "galType", "veldisp",
]


def paths(chip: int) -> tuple[Path, Path]:
    stem = f"CSST_MSC_MS_WIDE_20280101000000_20280101000230_10100300001_{chip}_L0_V01"
    return CSST_DATA / f"{stem}.fits", CSST_DATA / f"{stem}_top1000.cat"


def read_catalogue(path: Path) -> Table:
    return Table.read(path, format="ascii", names=CAT_NAMES, comment="#")


def image_only_crop(image: np.ndarray, size: int = SIZE) -> tuple[int, int, float]:
    """Choose a fixed-grid crop from image statistics only, never catalogue labels."""
    med = float(np.nanmedian(image))
    mad = float(1.4826 * np.nanmedian(np.abs(image - med)))
    threshold = med + 8.0 * max(mad, 1e-6)
    ny, nx = image.shape
    best = (-1, 0, 0)
    for y0 in range(0, ny - size + 1, size):
        for x0 in range(0, nx - size + 1, size):
            tile = image[y0:y0 + size, x0:x0 + size]
            score = int(np.count_nonzero(tile > threshold))
            if score > best[0]:
                best = (score, x0, y0)
    return best[1], best[2], mad


def one_to_one(det: np.ndarray, ref: np.ndarray, radius: float = 2.0):
    md = np.zeros(len(det), dtype=bool)
    mr = np.zeros(len(ref), dtype=bool)
    ridx = np.full(len(det), -1, dtype=int)
    if len(det) == 0 or len(ref) == 0:
        return md, mr, ridx
    tree = cKDTree(ref)
    k = min(8, len(ref))
    dist, idx = tree.query(det, k=k)
    if k == 1:
        dist = dist[:, None]
        idx = idx[:, None]
    di, kk = np.where(dist <= radius)
    candidates = np.c_[dist[di, kk], di, idx[di, kk]]
    candidates = candidates[np.argsort(candidates[:, 0])]
    for _, d, r in candidates:
        d, r = int(d), int(r)
        if not md[d] and not mr[r]:
            md[d], mr[r], ridx[d] = True, True, r
    return md, mr, ridx


def affine_fit(det: np.ndarray, ref: np.ndarray) -> np.ndarray:
    if len(det) < 3:
        return np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    A = np.c_[det, np.ones(len(det))]
    return np.vstack([np.linalg.lstsq(A, ref[:, j], rcond=None)[0] for j in range(2)])


def apply_affine(xy: np.ndarray, coeff: np.ndarray) -> np.ndarray:
    return np.c_[xy, np.ones(len(xy))] @ coeff.T


def branch_dao(image, rms):
    tab = DAOStarFinder(fwhm=3.0, threshold=3.0 * rms, exclude_border=True)(image)
    if tab is None or len(tab) == 0:
        return np.empty((0, 2)), np.empty(0)
    return np.c_[np.asarray(tab["xcentroid"], float), np.asarray(tab["ycentroid"], float)], np.asarray(tab["flux"], float)


def branch_sep(image):
    arr = np.ascontiguousarray(image.astype(np.float32))
    background = sep.Background(arr)
    sep.set_sub_object_limit(100000)
    obj = sep.extract(arr - background.back(), 3.0 * background.globalrms, minarea=5,
                      deblend_nthresh=32, deblend_cont=0.005)
    if len(obj) == 0:
        return np.empty((0, 2)), np.empty(0)
    return np.c_[obj["x"], obj["y"]], np.asarray(obj["flux"], float)


def branch_photutils(image, rms):
    finder = DAOStarFinder(fwhm=3.0, threshold=3.0 * rms, exclude_border=True)
    phot = PSFPhotometry(
        CircularGaussianPRF(fwhm=3.0), fit_shape=(9, 9), finder=finder,
        grouper=SourceGrouper(min_separation=2.0), aperture_radius=3.0,
        fitter_maxiters=20, group_warning_threshold=1000, progress_bar=False,
    )
    tab = phot(image)
    if len(tab) == 0:
        return np.empty((0, 2)), np.empty(0)
    good = np.isfinite(tab["x_fit"]) & np.isfinite(tab["y_fit"]) & np.isfinite(tab["flux_fit"]) & (tab["flux_fit"] > 0)
    return np.c_[np.asarray(tab["x_fit"][good], float), np.asarray(tab["y_fit"][good], float)], np.asarray(tab["flux_fit"][good], float)


def branch_epsf(image, rms, dao_tab, spatial_mode=False):
    psf, _ = epsf.build_epsf(image, dao_tab)
    initial = np.c_[dao_tab["xcentroid"], dao_tab["ycentroid"]]
    detections, _, _, _ = epsf.residual_candidates(image, rms, psf, initial)
    if spatial_mode:
        grid = spatial.build_quadrant_psfs(image, dao_tab)
        fit, flux = spatial.fit_catalogue_spatial(image, grid, detections, passes=2)
    else:
        fit, flux = epsf.fit_catalogue(image, psf, detections)
    good = np.isfinite(flux) & (flux > 0)
    return fit[good], flux[good]


def score(name, xy, flux, refxy, refmag, partitions):
    md0, _, ridx0 = one_to_one(xy, refxy)
    matched = int(md0.sum())
    train = partitions != 2
    test = partitions == 2
    # Registration and zero point use only non-test matches.
    train_det = md0 & (ridx0 >= 0) & train[np.maximum(ridx0, 0)]
    coeff = affine_fit(xy[train_det], refxy[ridx0[train_det]])
    aligned = apply_affine(xy, coeff)
    md, mr, ridx = one_to_one(aligned, refxy[test])
    matched_test_idx = np.where(md)[0]
    test_ref_idx = np.where(test)[0][ridx[md]] if md.any() else np.empty(0, int)
    tp = int(md.sum())
    position = float(np.sqrt(np.mean(np.sum((aligned[matched_test_idx] - refxy[test_ref_idx]) ** 2, axis=1))) * PIXEL_SCALE_MAS) if tp else float("nan")
    mag_rms = float("nan")
    if tp and len(flux) == len(xy):
        train_matches = md0 & (ridx0 >= 0) & train[np.maximum(ridx0, 0)] & np.isfinite(flux) & (flux > 0)
        if train_matches.sum() >= 5:
            inst_train = -2.5 * np.log10(np.maximum(flux[train_matches], 1e-6))
            zp = float(np.median(refmag[ridx0[train_matches]] - inst_train))
            inst = -2.5 * np.log10(np.maximum(flux[matched_test_idx], 1e-6))
            residual = inst + zp - refmag[test_ref_idx]
            residual = residual[np.isfinite(residual) & (np.abs(residual) < 2)]
            if len(residual):
                mag_rms = float(np.sqrt(np.mean((residual - np.mean(residual)) ** 2)))
    return {
        "method": name, "candidates": int(len(xy)), "test_references": int(test.sum()),
        "test_recovered": tp, "test_recovery": float(tp / max(1, test.sum())),
        "test_position_rms_mas": position, "test_magnitude_rms_mag": mag_rms,
        "all_catalogue_matches": matched,
    }


def run_chip(chip: int) -> list[dict]:
    image_path, cat_path = paths(chip)
    with fits.open(image_path, memmap=False) as hdul:
        image = np.asarray(hdul[1].data, dtype=float)
    x0, y0, global_rms = image_only_crop(image)
    crop = image[y0:y0 + SIZE, x0:x0 + SIZE]
    background = float(np.nanmedian(crop))
    work = crop - background
    rms = float(1.4826 * np.nanmedian(np.abs(work - np.nanmedian(work))))
    ref = read_catalogue(cat_path)
    rx = np.asarray(ref["xImage"], float) - x0
    ry = np.asarray(ref["yImage"], float) - y0
    inside = (rx >= 0) & (rx < SIZE) & (ry >= 0) & (ry < SIZE)
    refxy = np.c_[rx[inside], ry[inside]]
    refmag = np.asarray(ref["mag"], float)[inside]
    # Fixed spatial partition: no labels are used to choose candidates or fit.
    cx = np.floor(refxy[:, 0] / 200).astype(int)
    cy = np.floor(refxy[:, 1] / 200).astype(int)
    partitions = (cx + 2 * cy) % 3
    dao_tab = DAOStarFinder(fwhm=3.0, threshold=3.0 * rms, exclude_border=True)(work)
    if dao_tab is None or len(dao_tab) < 12:
        raise RuntimeError(f"chip {chip}: only {0 if dao_tab is None else len(dao_tab)} DAO PSF stamps")
    # The common crop can contain many noise peaks in the CSST detector
    # background.  Keep a fixed, image-only bright-candidate budget for the
    # ePSF branches so their nonlinear neighbour fits are bounded and do not
    # become an implicit chip-specific tuning exercise.
    if len(dao_tab) > 1500:
        order = np.argsort(np.asarray(dao_tab["flux"], float))[::-1][:1500]
        dao_tab = dao_tab[order]
    print(json.dumps({"chip": chip, "crop_x0": x0, "crop_y0": y0, "image_only_score": global_rms, "references": len(refxy), "rms": rms, "dao_candidates": len(dao_tab)}), flush=True)
    branches = [
        ("DAOStarFinder", lambda: branch_dao(work, rms)),
        ("SEP/SExtractor-style", lambda: branch_sep(work)),
        ("Photutils PSFPhotometry", lambda: branch_photutils(work, rms)),
        ("AstroCFR ePSF + residual deblend", lambda: branch_epsf(work, rms, dao_tab, False)),
        ("AstroCFR spatial-ePSF + joint fit", lambda: branch_epsf(work, rms, dao_tab, True)),
    ]
    rows = []
    for name, fn in branches:
        start = time.perf_counter()
        try:
            xy, flux = fn()
            row = score(name, xy, flux, refxy, refmag, partitions)
            row.update({"chip": chip, "crop_x0": x0, "crop_y0": y0, "runtime_s": time.perf_counter() - start, "status": "complete"})
            print(json.dumps(row), flush=True)
        except Exception as exc:
            row = {"chip": chip, "method": name, "status": "failed", "error": repr(exc), "runtime_s": time.perf_counter() - start}
            print(json.dumps(row), flush=True)
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chips", nargs="*", type=int, default=list(CHIPS))
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "csst_unified_five_methods")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for chip in args.chips:
        rows.extend(run_chip(chip))
    (args.output / "csst_unified_five_methods.json").write_text(json.dumps({
        "protocol": {
            "scope": "CSST-like simulated chips; fixed 1200x1200 crop selected from image-only 8-MAD excess-pixel score",
            "chips": list(args.chips), "association_radius_px": 2.0,
            "pixel_scale_mas": PIXEL_SCALE_MAS,
            "candidate_generation": "image-only; DAO/SEP/Photutils/ePSF branches use no reference labels; ePSF PSF-stamp budget is the 1500 brightest DAO proposals per crop",
            "registration": "non-test catalogue matches only; deterministic 200-pixel cell partition",
            "reference_use": "post-fit registration, photometric zero point, and final scoring only",
        }, "rows": rows
    }, indent=2), encoding="utf-8")
    fields = sorted({k for row in rows for k in row})
    with (args.output / "csst_unified_five_methods.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
