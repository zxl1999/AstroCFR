#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Evaluate the Windows DOLPHOT five-exposure NGC 6397 pilot.

This is deliberately a *diagnostic*, not a manuscript-result generator.  It
maps the DOLPHOT reference-frame coordinates through the native FLC WCS to the
ACSGGCT mosaic, learns the residual six-parameter transform and a F606W
zero-point only from non-test spatial cells, then evaluates the untouched
cells.  It therefore uses the same mosaic crop, catalogue quality flags,
association radius, and spatial split as ``hst_acsggct_benchmark.py``.

The input FLC files and the DOLPHOT work directory are outside version
control.  This program writes a compact JSON/CSV/Markdown audit beside itself.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS
from scipy.spatial import cKDTree


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SRC = REPO / "src"
MODULES = SRC / "wpdc"
if str(MODULES) not in sys.path:
    # The established HST scripts import these research modules as scripts;
    # retain that convention because domain_adaptation uses the same import.
    sys.path.insert(0, str(MODULES))
import real_data_domain_adaptation as adapt  # noqa: E402
import real_data_zero_shot_generalization as base  # noqa: E402


CROP_X0 = CROP_Y0 = 2400
CROP_SIZE = 1200
PIXEL_SCALE_MAS = 50.0
MATCH_RADIUS_PX = 2.0
OUT = HERE / "dolphot_joint_ngc6397_results"


def find_one(root: Path, name: str) -> Path:
    hits = list(root.rglob(name))
    if len(hits) != 1:
        raise RuntimeError(f"Expected exactly one {name!r} below {root}, found {len(hits)}")
    return hits[0]


def greedy_match(det_xy: np.ndarray, ref_xy: np.ndarray, radius: float):
    return base.greedy_match(np.asarray(det_xy, float), np.asarray(ref_xy, float), radius)


def cell_ids(xy: np.ndarray) -> np.ndarray:
    return adapt.cell_ids(np.asarray(xy, float) - np.array([CROP_X0, CROP_Y0]), 200)


def fit_affine(det: np.ndarray, ref: np.ndarray):
    """Six-parameter fit with robust clipping, returning coefficients and mask."""
    if len(det) < 10:
        raise RuntimeError(f"Only {len(det)} alignment matches; need at least 10")
    good = np.ones(len(det), dtype=bool)
    for _ in range(6):
        A = np.column_stack([np.ones(np.sum(good)), det[good, 0], det[good, 1]])
        cx, *_ = np.linalg.lstsq(A, ref[good, 0], rcond=None)
        cy, *_ = np.linalg.lstsq(A, ref[good, 1], rcond=None)
        all_a = np.column_stack([np.ones(len(det)), det[:, 0], det[:, 1]])
        pred = np.column_stack([all_a @ cx, all_a @ cy])
        residual = np.hypot(*(pred - ref).T)
        med = np.median(residual[good])
        scale = 1.4826 * np.median(np.abs(residual[good] - med))
        next_good = residual <= med + max(3.0 * scale, 0.15)
        if np.array_equal(next_good, good):
            break
        good = next_good
    return (cx, cy), good


def apply_affine(xy: np.ndarray, coeff) -> np.ndarray:
    A = np.column_stack([np.ones(len(xy)), xy[:, 0], xy[:, 1]])
    return np.column_stack([A @ coeff[0], A @ coeff[1]])


def robust_rms(values: np.ndarray) -> tuple[float | None, int]:
    values = np.asarray(values, float)
    if len(values) < 5:
        return None, int(len(values))
    med = np.median(values)
    scale = 1.4826 * np.median(np.abs(values - med))
    good = np.abs(values - med) <= max(3.0 * scale, 1e-4)
    if np.sum(good) < 5:
        return None, int(np.sum(good))
    return float(np.sqrt(np.mean((values[good] - np.mean(values[good])) ** 2))), int(np.sum(good))


def translation_peak(det: np.ndarray, refs: np.ndarray) -> np.ndarray:
    """Estimate the WCS residual translation from a cross-match offset peak."""
    pairs = cKDTree(refs).query_ball_point(det, r=30.0)
    offsets = np.vstack([refs[j] - det[i] for i, near in enumerate(pairs) for j in near])
    if len(offsets) < 10:
        raise RuntimeError("Insufficient WCS-neighbour pairs for translation estimate")
    hist, xe, ye = np.histogram2d(offsets[:, 0], offsets[:, 1], bins=240,
                                  range=[[-30, 30], [-30, 30]])
    row, col = np.unravel_index(np.argmax(hist), hist.shape)
    peak = np.array([(xe[row] + xe[row + 1]) / 2, (ye[col] + ye[col + 1]) / 2])
    around = np.all(np.abs(offsets - peak) <= 0.75, axis=1)
    return np.median(offsets[around], axis=0)


def catalogue_subsets(cat: Table):
    x = np.asarray(cat["x"], float) - 1.0  # ACSGGCT is one-indexed.
    y = np.asarray(cat["y"], float) - 1.0
    mag = np.asarray(cat["Vvega"], float)
    inside = ((x >= CROP_X0 + 12) & (x < CROP_X0 + CROP_SIZE - 12) &
              (y >= CROP_Y0 + 12) & (y < CROP_Y0 + CROP_SIZE - 12))
    measured = inside & (mag < 90)
    quality = (measured & (np.asarray(cat["err"], float) < 0.10) &
               (np.asarray(cat["qfitV"], float) < 0.30) &
               (np.asarray(cat["othv"], float) < 1.0) &
               (np.asarray(cat["Nv"], int) >= 1))
    calibration = (quality & (mag <= 21.0) & (np.asarray(cat["err"], float) < 0.05) &
                   (np.asarray(cat["qfitV"], float) < 0.15) &
                   (np.asarray(cat["othv"], float) < 0.10))
    return x, y, mag, measured, quality, calibration


def main():
    csst_dir = next(path for path in REPO.parent.glob("CSST_*") if path.is_dir())
    base_data = find_one(csst_dir, "hlsp_acsggct_hst_acs-wfc_ngc6397_f606w_v2_img.fits").parent
    catalogue_path = find_one(csst_dir, "hlsp_acsggct_hst_acs-wfc_ngc6397_r.rdviq.cal.adj.zpt")
    flc_path = find_one(csst_dir, "j9l965cnq_flc.fits")
    work = REPO / "external" / "dolphot" / "joint_ngc6397"
    joint_path = work / "joint"
    if not joint_path.exists():
        raise FileNotFoundError(f"Missing independent DOLPHOT output: {joint_path}")

    # DOLPHOT columns 1--25: extension, chip, x, y, chi, S/N, sharpness,
    # roundness, angle, crowding, type, pass, total-counts ... Vega mag, mag error.
    raw = np.loadtxt(joint_path, usecols=range(25), dtype=float)
    extension, xy_flc = raw[:, 0].astype(int), raw[:, 2:4]
    chi, snr, sharp, crowding, objtype = raw[:, 4], raw[:, 5], raw[:, 6], raw[:, 9], raw[:, 10].astype(int)
    vegamag, magerr = raw[:, 16], raw[:, 18]

    mosaic_path = base_data / "hlsp_acsggct_hst_acs-wfc_ngc6397_f606w_v2_img.fits"
    mosaic_wcs = WCS(fits.getheader(mosaic_path))
    xy_wcs = np.full_like(xy_flc, np.nan)
    # The reference exposure is j9l965cnq_flc.  FLC SCI 1/4 correspond to
    # DOLPHOT extensions 1/2; fobj is essential for CPDIS lookup tables.
    with fits.open(flc_path, memmap=False) as flc:
        for ext, hdu in ((1, 1), (2, 4)):
            mask = extension == ext
            fwcs = WCS(flc[hdu].header, fobj=flc)
            xy_wcs[mask] = mosaic_wcs.all_world2pix(fwcs.all_pix2world(xy_flc[mask], 1), 1)

    cat = Table.read(catalogue_path, format="ascii")
    x, y, refmag, measured, quality, calibration = catalogue_subsets(cat)
    q_xy = np.column_stack([x[quality], y[quality]])
    q_mag = refmag[quality]
    q_part = cell_ids(q_xy)
    cal_xy = np.column_stack([x[calibration], y[calibration]])
    cal_part = cell_ids(cal_xy)
    cal_train = cal_xy[cal_part != 2]

    # Estimate only from non-test cells.  The quality cuts are declared before
    # matching and are not optimized on test results.
    align_src = ((objtype <= 2) & (snr >= 10) & (vegamag > 10) & (vegamag < 21) &
                 (chi < 3) & (crowding < 1) &
                 (xy_wcs[:, 0] >= CROP_X0 - 50) & (xy_wcs[:, 0] < CROP_X0 + CROP_SIZE + 50) &
                 (xy_wcs[:, 1] >= CROP_Y0 - 50) & (xy_wcs[:, 1] < CROP_Y0 + CROP_SIZE + 50) &
                 (cell_ids(xy_wcs) != 2))
    translated = xy_wcs.copy()
    translations = {}
    for ext in (1, 2):
        use = align_src & (extension == ext)
        shift = translation_peak(xy_wcs[use], cal_train)
        translated[extension == ext] += shift
        translations[str(ext)] = [float(v) for v in shift]

    initial = align_src
    imatch, iref = greedy_match(translated[initial], cal_train, 3.0)
    det_i = np.flatnonzero(initial)[imatch]
    coeff, inlier = fit_affine(translated[det_i], cal_train[iref[imatch]])
    xy_final = apply_affine(translated, coeff)

    # A fixed DOLPHOT science-catalogue quality selection, used for both
    # recovery and measurements.  It excludes explicit artifacts (types 3--5)
    # while retaining standard bright/faint stellar types 1/2.
    keep = ((objtype <= 2) & (snr >= 5) & (chi <= 2.5) & (np.abs(sharp) <= 0.5) &
            (crowding <= 1.0) & (magerr <= 0.10) & (vegamag < 90) &
            (xy_final[:, 0] >= CROP_X0) & (xy_final[:, 0] < CROP_X0 + CROP_SIZE) &
            (xy_final[:, 1] >= CROP_Y0) & (xy_final[:, 1] < CROP_Y0 + CROP_SIZE))
    det_part = cell_ids(xy_final)
    test_ref = q_part == 2
    test_det = keep & (det_part == 2)
    recalled, _ = greedy_match(xy_final[test_det], q_xy[test_ref], MATCH_RADIUS_PX)

    # Match all retained candidates, fit zero-point only from train matches,
    # then assess astrometry and photometry only on untouched test matches.
    match, ridx = greedy_match(xy_final[keep], q_xy, MATCH_RADIUS_PX)
    keep_idx = np.flatnonzero(keep)
    didx = keep_idx[match]
    rsel = ridx[match]
    train = q_part[rsel] != 2
    test = q_part[rsel] == 2
    if np.sum(train) < 10 or np.sum(test) < 5:
        raise RuntimeError(f"Insufficient matched train/test stars: {np.sum(train)}/{np.sum(test)}")
    zp = float(np.median(q_mag[rsel][train] - vegamag[didx][train]))
    delta = xy_final[didx][test] - q_xy[rsel][test]
    radial = np.hypot(delta[:, 0], delta[:, 1])
    radial_rms, ast_n = robust_rms(radial)
    # Convert radial RMS to a one-dimensional RMS, consistent with the existing
    # benchmark; the residual is already referenced to a train-only transform.
    ast_1d = None if radial_rms is None else radial_rms / np.sqrt(2.0)
    mag_resid = vegamag[didx][test] + zp - q_mag[rsel][test]
    phot_rms, phot_n = robust_rms(mag_resid)

    summary = {
        "status": "diagnostic_only",
        "cluster": "NGC 6397",
        "method": "DOLPHOT 2.1 ACS five-FLC joint run on Windows",
        "dolphot_rows": int(len(raw)),
        "retained_in_crop": int(np.sum(keep)),
        "quality_test_references": int(np.sum(test_ref)),
        "test_recall_quality": float(np.sum(recalled) / max(np.sum(test_ref), 1)),
        "test_matched_measurements": int(np.sum(test)),
        "astrometric_1d_rms_mas": None if ast_1d is None else float(ast_1d * PIXEL_SCALE_MAS),
        "astrometric_inliers": ast_n,
        "photometric_rms_mag_after_train_only_zp": phot_rms,
        "photometric_inliers": phot_n,
        "train_only_vega_zero_point_offset_mag": zp,
        "alignment_matches_preclip": int(len(det_i)),
        "alignment_inliers": int(np.sum(inlier)),
        "per_extension_wcs_translation_px": translations,
        "matching_radius_px": MATCH_RADIUS_PX,
        "crop": [CROP_X0, CROP_Y0, CROP_SIZE, CROP_SIZE],
        "critical_warning": "DOLPHOT joint.warnings reports 0.77--1.18 pixel inter-exposure alignment scatter for three 15-s frames; do not interpret this pilot as a high-precision multi-epoch baseline or include it in manuscript results.",
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with (OUT / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=summary.keys())
        writer.writeheader(); writer.writerow(summary)
    lines = [
        "# Windows DOLPHOT joint-run diagnostic: NGC 6397", "",
        "This is an external-backend reproducibility diagnostic, not an approved WPDC manuscript result.", "",
        "## Protocol", "",
        "Five native ACS/WFC F606W FLC exposures were copied into an ignored work directory, preprocessed with `acsmask` and `calcsky`, and processed with DOLPHOT 2.1 ACS. Coordinates were transformed from the reference FLC through its CPDIS-aware WCS into the ACSGGCT mosaic. A residual six-parameter affine transform and a scalar F606W zero point used only spatial partitions 0/1; partition 2 was untouched for all reported metrics.", "",
        f"- Test quality references: {summary['quality_test_references']}",
        f"- Retained DOLPHOT sources in central crop: {summary['retained_in_crop']}",
        f"- Test recovery at 2 px: {summary['test_recall_quality']:.4f}",
        f"- Test 1D astrometric RMS: {summary['astrometric_1d_rms_mas']:.2f} mas" if ast_1d is not None else "- Test 1D astrometric RMS: unavailable",
        f"- Test photometric RMS after train-only scalar zero point: {summary['photometric_rms_mag_after_train_only_zp']:.4f} mag" if phot_rms is not None else "- Test photometric RMS: unavailable",
        f"- Alignment matches / retained inliers: {summary['alignment_matches_preclip']} / {summary['alignment_inliers']}", "",
        "## Interpretation constraint", "",
        summary["critical_warning"],
        "The output must remain outside the main comparison table unless re-registration reduces this warning and an independently repeated test confirms the result.",
    ]
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
