#!/usr/bin/env python
"""Single-reference-image benchmark on the ANGST M81/NGC 2976 deep fields.

This is deliberately a catalogue-conditioned recovery/measurement benchmark,
not a blind-purity experiment.  All methods operate on the same official
F814W ANGST reference image.  The finite ANGST GST catalogue is used only for
evaluation and for fitting an affine registration and a magnitude offset on
spatially disjoint non-test stripes.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import threading
import time
from pathlib import Path

import numpy as np
import psutil
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS
from scipy.spatial import cKDTree
from scipy.signal import convolve2d
from scipy.stats import binomtest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src" / "wpdc"))
sys.path.insert(0, str(HERE))

import candidate_features
import hst_unified_baseline_benchmark as baseline
import real_data_zero_shot_generalization as imageops

DATA = ROOT / "external" / "non_globular_fields" / "angst_reference"
DEFAULT_OUT = ROOT / "results" / "non_globular_runs" / "angst_single_reference"
FIELDS = {
    "m81_deep": {"stem": "m81-deep", "catalogue_filters": "f606w-f814w", "band": "f814w"},
    "ngc2976_deep": {"stem": "ngc2976-deep", "catalogue_filters": "f606w-f814w", "band": "f814w"},
    "gr8": {"stem": "gr8", "catalogue_filters": "f475w-f814w", "band": "f475w"},
}
METHODS = ("dao", "sep", "photutils_psf", "astrocfr_epsf", "astrocfr_photutils_hybrid")
LABELS = {
    "dao": "DAOStarFinder",
    "sep": "SEP/SExtractor-style",
    "photutils_psf": "Photutils PSFPhotometry",
    "astrocfr_epsf": "AstroCFR ePSF + residual deblend",
    "astrocfr_photutils_hybrid": "AstroCFR ePSF proposals + Photutils PSFPhotometry",
}
MATCH_RADIUS = 2.0
PIXEL_SCALE_MAS = 50.0


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return [None, None]
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [float(max(0, mid - half)), float(min(1, mid + half))]


def measured(fn):
    proc = psutil.Process()
    start_rss = proc.memory_info().rss
    peak = [start_rss]
    stop = threading.Event()

    def poll():
        while not stop.wait(0.01):
            peak[0] = max(peak[0], proc.memory_info().rss)

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    start = time.perf_counter()
    try:
        value = fn()
        return value, time.perf_counter() - start, max(0, peak[0] - start_rss) / 1024**2
    finally:
        stop.set()
        thread.join()


def densest_crop(x, y, shape, crop_size):
    ny, nx = shape
    step = 80
    xb = max(1, int(np.ceil(nx / step)))
    yb = max(1, int(np.ceil(ny / step)))
    hist, _, _ = np.histogram2d(y, x, bins=(yb, xb), range=((0, ny), (0, nx)))
    ky = max(1, int(np.ceil(crop_size / (ny / yb))))
    kx = max(1, int(np.ceil(crop_size / (nx / xb))))
    score = convolve2d(hist, np.ones((ky, kx)), mode="same", boundary="fill")
    iy, ix = np.unravel_index(np.argmax(score), score.shape)
    cx = (ix + 0.5) * nx / xb
    cy = (iy + 0.5) * ny / yb
    x0 = int(np.clip(round(cx - crop_size / 2), 0, max(0, nx - crop_size)))
    y0 = int(np.clip(round(cy - crop_size / 2), 0, max(0, ny - crop_size)))
    return x0, y0


def load_gr8_flc(gst: Path, crop_size: int, crop_origin: tuple[int, int] | None = None,
                 image_name: str | None = None, image_extension: int | None = None):
    cat = fits.getdata(gst, 1)
    ra = np.asarray(cat["RA"], float)
    dec = np.asarray(cat["DEC"], float)
    mag = np.asarray(cat["MAG1_ACS"], float)
    err = np.asarray(cat["MAG1_ERR"], float)
    snr = np.asarray(cat["SNR1"], float)
    sharp = np.asarray(cat["SHARP1"], float)
    crowd = np.asarray(cat["CROWD1"], float)
    flag = np.asarray(cat["FLAG1"], int)
    quality = (np.isfinite(ra) & np.isfinite(dec) & np.isfinite(mag) &
               np.isfinite(err) & (mag < 90) & (err <= 0.10) & (snr >= 10) &
               (np.abs(sharp) <= 0.30) & (crowd <= 1.0) & (flag <= 2))
    ra, dec, mag = ra[quality], dec[quality], mag[quality]
    flcs = sorted((ROOT / "external/non_globular_fields/gr8/flc").glob("*_flc.fits"))
    best = None
    for path in flcs:
        if image_name is not None and path.name != image_name:
            continue
        with fits.open(path, memmap=True) as hdul:
            # ACS/WFC FLC layout is SCI, ERR, DQ for chip 1 followed by
            # SCI, ERR, DQ for chip 2.  Never treat ERR extension 2 as a
            # science image.
            for ext in (1, 4):
                if image_extension is not None and ext != image_extension:
                    continue
                shape = hdul[ext].data.shape
                wcs = WCS(hdul[ext].header, fobj=hdul)
                x, y = wcs.all_world2pix(ra, dec, 0)
                valid = (np.isfinite(x) & np.isfinite(y) & (x >= 0) & (x < shape[1]) &
                         (y >= 0) & (y < shape[0]))
                if not valid.any():
                    continue
                if crop_origin is None:
                    x0, y0 = densest_crop(x[valid], y[valid], shape, crop_size)
                else:
                    x0, y0 = crop_origin
                    if not (0 <= x0 <= shape[1] - crop_size and 0 <= y0 <= shape[0] - crop_size):
                        raise ValueError(f"GR8 crop origin {crop_origin} outside {shape} for crop {crop_size}")
                inside = valid & (x >= x0 + 12) & (x < x0 + crop_size - 12) & (y >= y0 + 12) & (y < y0 + crop_size - 12)
                candidate = (int(inside.sum()), path, ext, x0, y0, tuple(int(v) for v in shape),
                             x[inside] - x0, y[inside] - y0, mag[inside])
                if best is None or candidate[0] > best[0]:
                    best = candidate
    if best is None:
        raise RuntimeError("GR8 GST catalogue does not overlap the downloaded FLC exposures")
    count, path, ext, x0, y0, shape, x, y, mags = best
    with fits.open(path, memmap=True) as hdul:
        crop = np.asarray(hdul[ext].data[y0:y0 + crop_size, x0:x0 + crop_size], float).copy()
    finite = np.isfinite(crop)
    crop[~finite] = np.nanmedian(crop[finite])
    return crop, np.column_stack([x, y]), mags, {
        "real_image": str(path), "image_extension": int(ext),
        "crop_origin_xy": [int(x0), int(y0)], "full_shape_yx": list(shape),
        "quality_references": int(count), "gst_catalogue": str(gst),
        "catalogue_band": "F475W", "image_source": "real CTE-corrected FLC SCI extension",
    }


def load_gr8_stack(stack_path: Path):
    """Load the auditable real multi-exposure/two-chip GR8 mosaic.

    The builder writes reference coordinates directly on the displayed mosaic
    grid, avoiding an artificial WCS across the deliberately separated chips.
    """
    refs_path = stack_path.with_name(stack_path.stem + "_references.npz")
    if not stack_path.exists() or not refs_path.exists():
        raise FileNotFoundError(f"GR8 stack requires {stack_path} and {refs_path}")
    with fits.open(stack_path, memmap=True) as hdul:
        raw = np.asarray(hdul[0].data, dtype=float).copy()
        header = hdul[0].header
    ref = np.load(refs_path)
    refs = np.asarray(ref["xy"], float)
    mags = np.asarray(ref["mag"], float)
    finite = np.isfinite(raw)
    raw[~finite] = np.nanmedian(raw[finite])
    return raw, refs, mags, {
        "real_image": str(stack_path), "reference_coordinates": str(refs_path),
        "quality_references": int(len(refs)), "catalogue_band": "F475W",
        "image_source": "real ACS/WFC F475W FLC SCI multi-exposure two-chip mosaic",
        "stack_protocol": header.get("STKPROTO", "see FITS HISTORY"),
        "input_exposures": header.get("NSTACK", 0),
    }


def load_field(field: str, crop_size: int, gr8_crop_origin: tuple[int, int] | None = None,
               gr8_image_name: str | None = None, gr8_image_extension: int | None = None,
               gr8_stack: Path | None = None):
    stem = FIELDS[field]["stem"]
    catalogue_filters = FIELDS[field]["catalogue_filters"]
    ref = DATA / f"hlsp_angst_hst_acs-wfc_10915-{stem}_f814w_v1_ref.fits"
    gst = DATA / f"hlsp_angst_hst_acs-wfc_10915-{stem}_{catalogue_filters}_v1_gst.fits"
    if field == "gr8" and gr8_stack is not None:
        return load_gr8_stack(gr8_stack)
    if field == "gr8" and (not ref.exists() or ref.stat().st_size < 10_000_000):
        return load_gr8_flc(gst, crop_size, gr8_crop_origin, gr8_image_name, gr8_image_extension)
    image = np.asarray(fits.getdata(ref), dtype=float)
    cat = fits.getdata(gst, 1)
    x0 = (image.shape[1] - crop_size) // 2
    y0 = (image.shape[0] - crop_size) // 2
    crop = image[y0:y0 + crop_size, x0:x0 + crop_size].copy()
    bad = ~np.isfinite(crop)
    if np.any(bad):
        crop[bad] = np.nanmedian(crop[~bad])

    # ANGST/DOLPHOT X,Y use half-integer pixel centres; NumPy/Photutils use
    # zero-based array coordinates.  Subtracting 0.5 puts both on one grid.
    x = np.asarray(cat["X"], float) - 0.5 - x0
    y = np.asarray(cat["Y"], float) - 0.5 - y0
    mag = np.asarray(cat["MAG2_ACS"], float)  # second filename filter: F814W
    err = np.asarray(cat["MAG2_ERR"], float)
    snr = np.asarray(cat["SNR2"], float)
    sharp = np.asarray(cat["SHARP2"], float)
    crowd = np.asarray(cat["CROWD2"], float)
    flag = np.asarray(cat["FLAG2"], int)
    inside = (x >= 12) & (x < crop_size - 12) & (y >= 12) & (y < crop_size - 12)
    # GST is already a DOLPHOT good-star selection.  These additional declared
    # cuts define a stable F814W measurement reference, not exhaustive truth.
    quality = (inside & np.isfinite(mag) & np.isfinite(err) & (mag < 90) &
               (err <= 0.10) & (snr >= 10) & (np.abs(sharp) <= 0.30) &
               (crowd <= 1.0) & (flag <= 2))
    refs = np.column_stack([x[quality], y[quality]])
    return crop, refs, mag[quality], {"reference_image": str(ref), "gst_catalogue": str(gst),
                                      "crop_origin_xy": [x0, y0], "full_shape_yx": list(image.shape),
                                      "quality_references": int(quality.sum())}


def spatial_partition(xy: np.ndarray):
    # Repeated vertical 200-pixel stripes: 0=fit, 1=validation, 2=held-out test.
    return (np.floor(np.asarray(xy)[:, 0] / 200).astype(int) % 3)


def one_to_one(det, ref):
    return imageops.greedy_match(np.asarray(det, float), np.asarray(ref, float), MATCH_RADIUS)


def robust_measurements(det, flux, refs, mags, partitions, pixel_scale_mas=PIXEL_SCALE_MAS):
    matched, ri = one_to_one(det, refs)
    di = np.where(matched)[0]
    ri = ri[matched]
    train = partitions[ri] != 2
    test = partitions[ri] == 2
    if train.sum() < 20 or test.sum() < 10:
        return {"measurement_test_matches": int(test.sum()), "astrometric_rms_mas": None,
                "photometric_rms_mag": None}

    coeff = baseline.old.fit_affine(det[di][train], refs[ri][train])
    pred = baseline.old.apply_affine(det[di][test], coeff)
    delta = pred - refs[ri][test]
    radial = np.hypot(delta[:, 0], delta[:, 1])
    med = np.median(radial)
    mad = 1.4826 * np.median(np.abs(radial - med))
    ast_keep = radial <= med + max(3 * mad, 0.05)
    ast_rms = np.sqrt(np.mean(radial[ast_keep] ** 2) / 2) * pixel_scale_mas

    inst = -2.5 * np.log10(np.maximum(np.asarray(flux, float), 1e-12))
    valid_train = train & np.isfinite(inst[di])
    zp = np.median(mags[ri][valid_train] - inst[di][valid_train])
    resid = inst[di][test] + zp - mags[ri][test]
    finite = np.isfinite(resid)
    resid = resid[finite]
    rmed = np.median(resid)
    rmad = 1.4826 * np.median(np.abs(resid - rmed))
    mag_keep = np.abs(resid - rmed) <= max(3 * rmad, 0.03)
    phot_rms = np.sqrt(np.mean((resid[mag_keep] - np.mean(resid[mag_keep])) ** 2))
    result = {"measurement_test_matches": int(test.sum()),
            "measurement_astrometric_inliers": int(ast_keep.sum()),
            "measurement_photometric_inliers": int(mag_keep.sum()),
            "astrometric_rms_mas": float(ast_rms),
            "photometric_rms_mag": float(phot_rms),
            "photometric_offset_fit_on_non_test": float(zp)}
    rng = np.random.default_rng(20260812 + len(det))
    ast_samples, mag_samples = [], []
    ast_values = radial[ast_keep]
    mag_values = resid[mag_keep]
    for _ in range(1000):
        a = rng.choice(ast_values, len(ast_values), replace=True)
        m = rng.choice(mag_values, len(mag_values), replace=True)
        ast_samples.append(np.sqrt(np.mean(a**2) / 2) * pixel_scale_mas)
        mag_samples.append(np.sqrt(np.mean((m - np.mean(m))**2)))
    result["astrometric_rms_mas_ci95"] = [float(v) for v in np.percentile(ast_samples, [2.5, 97.5])]
    result["photometric_rms_mag_ci95"] = [float(v) for v in np.percentile(mag_samples, [2.5, 97.5])]
    result["measurement_ci_scope"] = "conditional residual bootstrap after fixed matching/calibration/clipping"
    return result


def run_method(method, image, rms, fwhm):
    if method == "dao":
        return baseline.dao(image, rms, fwhm)
    if method == "sep":
        return baseline.sep_detect(image, rms)
    if method == "photutils_psf":
        return baseline.photutils_psf(image, rms, fwhm)
    if method == "astrocfr_epsf":
        return baseline.wpdc_deblend(image, rms, fwhm)
    if method == "astrocfr_photutils_hybrid":
        # Candidate recovery and final measurement are deliberately separate:
        # the image-derived AstroCFR ePSF/residual pass supplies all proposal
        # positions; Photutils is not allowed to rerun its DAO finder.  The
        # outer timer includes both stages.
        proposal_xy, proposal_flux = baseline.wpdc_deblend(image, rms, fwhm)
        if len(proposal_xy) == 0:
            return proposal_xy, proposal_flux
        init = Table()
        init["x_0"] = proposal_xy[:, 0]
        init["y_0"] = proposal_xy[:, 1]
        init["flux_0"] = np.maximum(np.asarray(proposal_flux, float), 1.0)
        from photutils.psf import CircularGaussianPRF, PSFPhotometry, SourceGrouper
        phot = PSFPhotometry(CircularGaussianPRF(fwhm=fwhm), fit_shape=(9, 9),
                             finder=None, grouper=SourceGrouper(min_separation=2.0),
                             aperture_radius=3.0, fitter_maxiters=30,
                             group_warning_threshold=1000, progress_bar=False)
        fitted = phot(image, init_params=init)
        good = (np.isfinite(fitted["x_fit"]) & np.isfinite(fitted["y_fit"]) &
                np.isfinite(fitted["flux_fit"]) & (fitted["flux_fit"] > 0))
        return (np.column_stack([np.asarray(fitted["x_fit"][good], float),
                                 np.asarray(fitted["y_fit"][good], float)]),
                np.asarray(fitted["flux_fit"][good], float))
    raise KeyError(method)


def evaluate(field, method, xy, flux, refs, mags, elapsed, memory, image_pixels,
             band_name="f814w", pixel_scale_mas=PIXEL_SCALE_MAS):
    ref_part = spatial_partition(refs)
    test_refs = ref_part == 2
    # Every image-only candidate may match a held-out reference.  Restricting
    # detections to a stripe would create artificial losses at stripe edges.
    match, _ = one_to_one(xy, refs[test_refs])
    k, n = int(match.sum()), int(test_refs.sum())
    out = {"field": field, "method": method, "label": LABELS[method],
           "candidates": int(len(xy)), "test_references": n, "test_recovered": k,
           "catalogue_recovery": k / max(n, 1), "catalogue_recovery_ci95": wilson(k, n),
           "runtime_s": float(elapsed), "runtime_s_per_mpix": float(elapsed / image_pixels * 1e6),
           "peak_rss_delta_mb": float(memory)}
    if method == "astrocfr_photutils_hybrid":
        out["candidate_stage"] = "AstroCFR ePSF + residual deblend"
        out["measurement_stage"] = "Photutils Gaussian-PRF PSFPhotometry"
        out["runtime_scope"] = "AstroCFR proposal plus Photutils final fit"
    for limit in (24, 26, 27, 28):
        subset = test_refs & (mags <= limit)
        m, _ = one_to_one(xy, refs[subset])
        out[f"recovery_{band_name}_le_{limit}"] = float(m.sum() / max(subset.sum(), 1))
        out[f"n_{band_name}_le_{limit}"] = int(subset.sum())
    tree = cKDTree(refs)
    density = np.asarray([len(tree.query_ball_point(p, 10)) - 1 for p in refs])
    dense = test_refs & (mags <= 27) & (density >= 3)
    m, _ = one_to_one(xy, refs[dense])
    out[f"dense_{band_name}_le_27_n"] = int(dense.sum())
    out[f"dense_{band_name}_le_27_recovery"] = float(m.sum() / max(dense.sum(), 1))
    out.update(robust_measurements(xy, flux, refs, mags, ref_part, pixel_scale_mas))
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--field", choices=tuple(FIELDS) + ("all",), default="all")
    parser.add_argument("--methods", nargs="+", choices=METHODS,
                        help="optional method subset for staged, auditable runs")
    parser.add_argument("--crop-size", type=int, default=1200)
    parser.add_argument("--gr8-crop-origin", type=int, nargs=2, metavar=("X0", "Y0"),
                        help="fixed GR8 FLC crop origin; permits an auditable reference-support selection")
    parser.add_argument("--gr8-image-name", help="restrict GR8 to one downloaded FLC filename")
    parser.add_argument("--gr8-image-extension", type=int, choices=(1, 4),
                        help="restrict GR8 to one SCI extension")
    parser.add_argument("--gr8-stack", type=Path,
                        help="real multi-exposure GR8 mosaic produced by build_gr8_multiepoch_mosaic.py")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    fields = FIELDS if args.field == "all" else (args.field,)
    rows, audits, paired_tests = [], {}, []
    for field in fields:
        print(f"Loading {field}", flush=True)
        raw, refs, mags, audit = load_field(
            field, args.crop_size,
            tuple(args.gr8_crop_origin) if args.gr8_crop_origin else None,
            args.gr8_image_name, args.gr8_image_extension, args.gr8_stack)
        image, rms = imageops.estimate_background(raw)
        bright = imageops.detect_sources(image, rms, fwhm=2.0, threshold_sigma=10.0)
        fwhm = float(np.clip(candidate_features.estimate_psf_fwhm(
            image, bright, rms, min_snr=20, max_sources=40), 1.5, 4.0))
        audit.update({"background_rms_electrons": float(rms), "image_only_fwhm_px": fwhm,
                      "image_shape_yx": list(image.shape), "pixel_scale_mas": PIXEL_SCALE_MAS})
        audits[field] = audit
        field_detections = {}
        for method in (tuple(args.methods) if args.methods else METHODS):
            print(f"{field}: {method}", flush=True)
            try:
                (xy, flux), elapsed, memory = measured(lambda: run_method(method, image, rms, fwhm))
                field_detections[method] = xy
                np.savez_compressed(args.output_dir / f"{field}_{method}_catalogue.npz", xy=xy, flux=flux)
                rows.append(evaluate(field, method, xy, flux, refs, mags,
                                     elapsed, memory, image.size,
                                     band_name=FIELDS[field]["band"]))
            except Exception as exc:
                rows.append({"field": field, "method": method, "label": LABELS[method],
                             "error": f"{type(exc).__name__}: {exc}"})
        if "astrocfr_epsf" in field_detections and "photutils_psf" in field_detections:
            test_refs = refs[spatial_partition(refs) == 2]
            a, _ = one_to_one(test_refs, field_detections["astrocfr_epsf"])
            p, _ = one_to_one(test_refs, field_detections["photutils_psf"])
            a_only = int(np.sum(a & ~p)); p_only = int(np.sum(p & ~a)); discordant = a_only + p_only
            rng = np.random.default_rng(20260812 + len(test_refs)); diffs = []
            paired = a.astype(float) - p.astype(float)
            for _ in range(10000):
                diffs.append(float(np.mean(rng.choice(paired, len(paired), replace=True))))
            paired_tests.append({"field": field, "method_a": "astrocfr_epsf", "method_b": "photutils_psf",
                                 "test_references": int(len(test_refs)), "a_only": a_only, "b_only": p_only,
                                 "paired_recovery_difference": float(np.mean(paired)),
                                 "paired_bootstrap_ci95": [float(v) for v in np.percentile(diffs, [2.5, 97.5])],
                                 "mcnemar_exact_p": (float(binomtest(min(a_only, p_only), discordant, 0.5,
                                                                      alternative="two-sided").pvalue)
                                                     if discordant else 1.0)})
    protocol = {
        "scope": "single real ANGST reference image or registered FLC crop; catalogue-conditioned evaluation",
        "truth_caveat": "finite DOLPHOT-derived ANGST GST catalogue; not exhaustive truth and not blind purity",
        "quality_reference": "GST quality cuts in the image band: error<=0.10, SNR>=10, |SHARP|<=0.30, CROWD<=1, FLAG<=2",
        "coordinate_convention": "reference-image X/Y or catalogue RA/Dec mapped through the real FLC WCS; common NumPy pixel grid",
        "association_radius_px": MATCH_RADIUS,
        "spatial_protocol": "200-pixel vertical stripes modulo 3; partition 2 held out",
        "measurement_protocol": "affine registration and F814W magnitude offset fit on non-test matches; RMS on held-out matches",
        "position_metric": "conditional one-coordinate-equivalent RMS after robust clipping, at 50 mas/pixel",
        "photometry_metric": "conditional F814W residual scatter after robust clipping; not error relative to physical truth",
    }
    payload = {"protocol": protocol, "field_audit": audits, "results": rows,
               "paired_recovery_tests": paired_tests}
    (args.output_dir / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if rows:
        fields_csv = sorted({key for row in rows for key in row})
        with (args.output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields_csv)
            writer.writeheader(); writer.writerows(rows)
    print(json.dumps(rows, indent=2), flush=True)


if __name__ == "__main__":
    main()
