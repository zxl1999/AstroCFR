#!/usr/bin/env python
"""Real-image comparison on Hubble Tarantula Treasury F555W pointings.

The image input is a real ACS/WFC FLC science extension.  The evaluation
catalogue is the published HTTP photometric catalogue (Sabbi et al. 2016,
VizieR J/ApJS/222/11/photcat), spatially queried around each registered
pointing and filtered before method evaluation.  It is finite external
photometry, not exhaustive truth; therefore unmatched detections are not
labelled false positives.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from scipy.stats import binomtest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src" / "wpdc"))
sys.path.insert(0, str(HERE))

import angst_non_globular_baseline as common
import candidate_features
from phat_real_catalogue_benchmark import densest_crop
import real_data_zero_shot_generalization as imageops

FIELDS = ("ngc2070_1", "ngc2070_2")
CROP_SIZE = 1200


def read_vizier_tsv(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("HTTP\tF555mag"))
    # VizieR emits a units row and a dashed separator after the header.
    # Preserve the actual column names explicitly instead of letting the first
    # data row become DictReader.fieldnames.
    fieldnames = lines[start].split("\t")
    reader = csv.DictReader(lines[start + 3:], fieldnames=fieldnames, delimiter="\t")
    rows = []
    for row in reader:
        try:
            mag = float(row["F555mag"]); err = float(row["e_F555mag"])
            qfit = float(row["q_F555mag"]); flag = int(row["f_F555mag"])
            ra = float(row["RAJ2000"]); dec = float(row["DEJ2000"])
        except (KeyError, TypeError, ValueError):
            continue
        if np.isfinite(mag) and np.isfinite(err) and np.isfinite(ra) and np.isfinite(dec) and mag < 90 and err <= 0.10 and qfit > 0.75 and flag == 1:
            rows.append((ra, dec, mag))
    if not rows:
        raise RuntimeError(f"no declared-quality F555W references in {path}")
    arr = np.asarray(rows, float)
    return arr[:, 0], arr[:, 1], arr[:, 2]


def choose_crop(field: str, catalogue: Path, crop_size: int):
    ra, dec, mag = read_vizier_tsv(catalogue)
    best = None
    for path in sorted((ROOT / "external/non_globular_fields" / field / "flc").glob("*_flc.fits")):
        with fits.open(path, memmap=True) as hdul:
            # ACS/WFC FLC ordering is SCI, ERR, DQ for chip 1 and again for
            # chip 2.  Only SCI extensions are valid image inputs.
            for ext in (1, 4):
                shape = hdul[ext].data.shape
                wcs = WCS(hdul[ext].header, fobj=hdul)
                x, y = wcs.all_world2pix(ra, dec, 0)
                valid = (np.isfinite(x) & np.isfinite(y) & (x >= 0) & (x < shape[1]) &
                         (y >= 0) & (y < shape[0]))
                if not valid.any():
                    continue
                x0, y0 = densest_crop(x[valid], y[valid], shape, crop_size)
                inside = valid & (x >= x0 + 12) & (x < x0 + crop_size - 12) & (y >= y0 + 12) & (y < y0 + crop_size - 12)
                candidate = (int(inside.sum()), path, ext, x0, y0,
                             tuple(int(v) for v in shape), wcs,
                             x[inside] - x0, y[inside] - y0, mag[inside])
                if best is None or candidate[0] > best[0]:
                    best = candidate
    if best is None:
        raise RuntimeError(f"HTTP catalogue does not overlap downloaded {field} FLCs")
    count, path, ext, x0, y0, shape, wcs, x, y, mags = best
    with fits.open(path, memmap=True) as hdul:
        raw = np.asarray(hdul[ext].data[y0:y0 + crop_size, x0:x0 + crop_size], float).copy()
    finite = np.isfinite(raw)
    raw[~finite] = np.nanmedian(raw[finite])
    scale = float(np.mean(proj_plane_pixel_scales(wcs.celestial)) * 3.6e6)
    audit = {
        "real_image": str(path.relative_to(ROOT)).replace("\\", "/"),
        "image_extension": int(ext), "crop_origin_xy": [int(x0), int(y0)],
        "full_shape_yx": list(shape), "quality_references": int(count),
        "pixel_scale_mas": scale, "catalogue_band": "F555W",
        "external_catalogue": str(catalogue.relative_to(ROOT)).replace("\\", "/"),
        "catalogue_source": "VizieR J/ApJS/222/11/photcat; Sabbi et al. 2016",
    }
    return raw, np.column_stack([x, y]), mags, audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--field", choices=FIELDS, required=True)
    parser.add_argument("--catalogue", type=Path)
    parser.add_argument("--crop-size", type=int, default=CROP_SIZE)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    catalogue = args.catalogue or ROOT / "external/reference_catalogs/http" / f"{args.field}_http_f555w_quality.tsv"
    output = args.output_dir or ROOT / "results/real_field_4plus10" / args.field
    output.mkdir(parents=True, exist_ok=True)
    raw, refs, mags, audit = choose_crop(args.field, catalogue, args.crop_size)
    image, rms = imageops.estimate_background(raw)
    bright = imageops.detect_sources(image, rms, fwhm=2.0, threshold_sigma=10.0)
    fwhm = float(np.clip(candidate_features.estimate_psf_fwhm(image, bright, rms, min_snr=20, max_sources=40), 1.5, 4.0))
    audit.update({"background_rms_electrons": float(rms), "image_only_fwhm_px": fwhm,
                  "truth_caveat": "finite published HTTP catalogue; catalogue-conditioned recovery only"})
    rows = []
    detections = {}
    for method in common.METHODS:
        print(f"{args.field}: {method}", flush=True)
        try:
            (xy, flux), elapsed, memory = common.measured(lambda m=method: common.run_method(m, image, rms, fwhm))
            detections[method] = xy
            np.savez_compressed(output / f"{method}_catalogue.npz", xy=xy, flux=flux)
            rows.append(common.evaluate(args.field, method, xy, flux, refs, mags, elapsed, memory,
                                        args.crop_size, band_name="f555w",
                                        pixel_scale_mas=audit["pixel_scale_mas"]))
        except Exception as exc:
            rows.append({"field": args.field, "method": method, "label": common.LABELS[method],
                         "error": f"{type(exc).__name__}: {exc}"})
    paired = []
    if "astrocfr_epsf" in detections and "photutils_psf" in detections:
        test_refs = refs[common.spatial_partition(refs) == 2]
        a, _ = common.one_to_one(test_refs, detections["astrocfr_epsf"])
        p, _ = common.one_to_one(test_refs, detections["photutils_psf"])
        a_only = int(np.sum(a & ~p)); p_only = int(np.sum(p & ~a)); discordant = a_only + p_only
        values = a.astype(float) - p.astype(float)
        rng = np.random.default_rng(20260813 + len(test_refs))
        boot = [float(np.mean(rng.choice(values, len(values), replace=True))) for _ in range(10000)]
        paired.append({"field": args.field, "method_a": "astrocfr_epsf", "method_b": "photutils_psf",
                       "test_references": int(len(test_refs)), "a_only": a_only, "b_only": p_only,
                       "paired_recovery_difference": float(np.mean(values)),
                       "paired_bootstrap_ci95": [float(v) for v in np.percentile(boot, [2.5, 97.5])],
                       "mcnemar_exact_p": float(binomtest(min(a_only, p_only), discordant, 0.5).pvalue) if discordant else 1.0})
    payload = {
        "protocol": {"scope": "single real HTTP ACS/WFC F555W FLC crop; no simulated scene",
                     "methods": list(common.METHODS), "association_radius_px": common.MATCH_RADIUS,
                     "reporting_boundary": "finite published catalogue; no blind precision/FDR label"},
        "field_audit": audit, "results": rows, "paired_recovery_tests": paired,
    }
    (output / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    keys = sorted({key for row in rows for key in row})
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys); writer.writeheader(); writer.writerows(rows)
    print(output / "summary.json")


if __name__ == "__main__":
    main()
