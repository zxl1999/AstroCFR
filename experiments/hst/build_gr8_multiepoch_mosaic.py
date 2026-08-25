#!/usr/bin/env python
"""Build an auditable real-data GR8 ACS/WFC F475W multi-exposure mosaic.

This deliberately makes no synthetic stars or catalogue-informed image edits.
Each chip is placed on the native pixel grid of one real FLC SCI extension;
the other CTE-corrected FLC SCI images are WCS-resampled onto that grid and a
per-pixel median is taken.  The two native-chip crops are then placed side by
side in a display mosaic.  GST catalogue positions are transformed through
the same two target WCS objects and saved separately for evaluation.

The output is a distinct *multi-exposure* protocol and must not be mixed with
the manuscript's single-reference-image rows without this label.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from scipy.ndimage import map_coordinates
from scipy.signal import convolve2d

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FLC_DIR = ROOT / "external/non_globular_fields/gr8/flc"
GST = ROOT / "external/non_globular_fields/angst_reference/hlsp_angst_hst_acs-wfc_10915-gr8_f475w-f814w_v1_gst.fits"


def densest_crop(x: np.ndarray, y: np.ndarray, shape: tuple[int, int], size: int) -> tuple[int, int]:
    ny, nx = shape
    step = 80
    bx, by = max(1, int(np.ceil(nx / step))), max(1, int(np.ceil(ny / step)))
    hist, _, _ = np.histogram2d(y, x, bins=(by, bx), range=((0, ny), (0, nx)))
    ky, kx = max(1, int(np.ceil(size / (ny / by)))), max(1, int(np.ceil(size / (nx / bx))))
    score = convolve2d(hist, np.ones((ky, kx)), mode="same", boundary="fill")
    iy, ix = np.unravel_index(np.argmax(score), score.shape)
    return (int(np.clip(round((ix + .5) * nx / bx - size / 2), 0, nx - size)),
            int(np.clip(round((iy + .5) * ny / by - size / 2), 0, ny - size)))


def quality_catalogue():
    cat = fits.getdata(GST, 1)
    q = (np.isfinite(cat["RA"]) & np.isfinite(cat["DEC"]) & np.isfinite(cat["MAG1_ACS"]) &
         (cat["MAG1_ACS"] < 90) & (cat["MAG1_ERR"] <= .10) & (cat["SNR1"] >= 10) &
         (np.abs(cat["SHARP1"]) <= .30) & (cat["CROWD1"] <= 1.0) & (cat["FLAG1"] <= 2))
    return np.asarray(cat["RA"][q], float), np.asarray(cat["DEC"][q], float), np.asarray(cat["MAG1_ACS"][q], float)


def valid_inputs():
    out = []
    for p in sorted(FLC_DIR.glob("*_flc.fits")):
        with fits.open(p, memmap=True) as h:
            exp = float(h[0].header.get("EXPTIME", 0))
            if exp <= 0:
                print(f"exclude {p.name}: non-positive EXPTIME={exp}")
                continue
            if h[0].header.get("FILTER1") != "F475W":
                continue
        out.append(p)
    if len(out) < 2:
        raise RuntimeError("fewer than two valid GR8 F475W FLC inputs")
    return out


def make_chip(target_path: Path, target_ext: int, paths: list[Path], ra, dec, mag, size: int):
    with fits.open(target_path, memmap=True) as h:
        th = h[target_ext].header.copy(); shape = h[target_ext].data.shape
        twcs = WCS(th, fobj=h)
    tx, ty = twcs.all_world2pix(ra, dec, 0)
    good = (tx >= 0) & (tx < shape[1]) & (ty >= 0) & (ty < shape[0])
    x0, y0 = densest_crop(tx[good], ty[good], shape, size)
    yy, xx = np.indices((size, size), dtype=float)
    world_ra, world_dec = twcs.all_pix2world(xx + x0, yy + y0, 0)
    planes = []
    for p in paths:
        # Each FLC has two science chips. Pick the chip that supplies the
        # largest valid footprint of this target crop (normally its matching
        # detector chip; this remains valid across small dithers).
        best = None
        with fits.open(p, memmap=True) as h:
            for ext in (1, 4):
                swcs = WCS(h[ext].header, fobj=h)
                sx, sy = swcs.all_world2pix(world_ra, world_dec, 0)
                valid = (sx >= 1) & (sx < h[ext].data.shape[1] - 2) & (sy >= 1) & (sy < h[ext].data.shape[0] - 2)
                count = int(valid.sum())
                if best is None or count > best[0]:
                    best = (count, ext, sx, sy)
            count, ext, sx, sy = best
            plane = map_coordinates(np.asarray(h[ext].data, float), [sy, sx], order=1, mode="constant", cval=np.nan)
            valid = (sx >= 1) & (sx < h[ext].data.shape[1] - 2) & (sy >= 1) & (sy < h[ext].data.shape[0] - 2)
            plane[~valid] = np.nan
        planes.append(plane)
        print(f"target SCI{target_ext}: {p.name} SCI{ext}, footprint={count}/{size*size}")
    stack = np.nanmedian(np.stack(planes), axis=0)
    # Reference positions must stay away from crop edges for the benchmark.
    inside = good & (tx >= x0 + 12) & (tx < x0 + size - 12) & (ty >= y0 + 12) & (ty < y0 + size - 12)
    refxy = np.column_stack([tx[inside] - x0, ty[inside] - y0])
    return stack, refxy, mag[inside], {"target_file": target_path.name, "target_sci_extension": target_ext,
                                       "crop_origin_xy": [x0, y0], "reference_count": int(inside.sum())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=1200)
    ap.add_argument("--output", type=Path, default=ROOT / "external/non_globular_fields/gr8/gr8_f475w_multiepoch_mosaic.fits")
    args = ap.parse_args()
    paths = valid_inputs(); ra, dec, mag = quality_catalogue()
    # One target exposure defines a stable physical native grid per chip.
    target = paths[-1]
    chips = [make_chip(target, ext, paths, ra, dec, mag, args.size) for ext in (1, 4)]
    mosaic = np.hstack([chips[0][0], chips[1][0]])
    xy = np.vstack([chips[0][1], chips[1][1] + np.array([args.size, 0.0])])
    mags = np.concatenate([chips[0][2], chips[1][2]])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    hdr = fits.Header()
    hdr["STKPROTO"] = "native-grid WCS linear-resample, median of real FLC SCI"
    hdr["NSTACK"] = len(paths)
    hdr["FILTER"] = "F475W"
    hdr["SCENARIO"] = "GR8 real ACS/WFC multi-exposure, two native SCI crops"
    hdr.add_history("Excluded j9ra0hftq_flc.fits because PRIMARY EXPTIME was zero.")
    for p in paths: hdr.add_history(f"Input real FLC: {p.name}")
    for c in chips: hdr.add_history(f"target={c[3]['target_file']} SCI={c[3]['target_sci_extension']} crop={c[3]['crop_origin_xy']} refs={c[3]['reference_count']}")
    fits.PrimaryHDU(mosaic.astype(np.float32), header=hdr).writeto(args.output, overwrite=True)
    np.savez_compressed(args.output.with_name(args.output.stem + "_references.npz"), xy=xy, mag=mags)
    print(f"wrote {args.output}; shape={mosaic.shape}; quality refs={len(xy)}; chips={[x[3] for x in chips]}")

if __name__ == "__main__": main()
