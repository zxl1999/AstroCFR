#!/usr/bin/env python
"""Compute detector-coordinate dither shifts relative to a reference FLC."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS


def wcs_points(path: Path):
    with fits.open(path, memmap=False) as hdul:
        wcs = WCS(hdul[1].header, fobj=hdul)
    # Detector points are used rather than one centre pixel so a residual
    # affine term is visible in the recorded manifest.
    return np.array([[512, 512], [2048, 512], [3584, 512],
                     [512, 1536], [2048, 1536], [3584, 1536]], float)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--images", type=Path, nargs="+", required=True)
    args = parser.parse_args()
    points = wcs_points(args.reference)
    with fits.open(args.reference, memmap=False) as hdul:
        rwcs = WCS(hdul[1].header, fobj=hdul)
        world = rwcs.all_pix2world(points, 1)
    out = {}
    for image in args.images:
        with fits.open(image, memmap=False) as hdul:
            iwcs = WCS(hdul[1].header, fobj=hdul)
            mapped = iwcs.all_world2pix(world, 1)
        delta = np.median(mapped - points, axis=0)
        residual = mapped - points - delta
        out[image.stem] = {"x": float(delta[0]), "y": float(delta[1]),
                           "residual_rms_px": float(np.sqrt(np.mean(residual**2))),
                           "mapped_points": int(len(points))}
    print(json.dumps(out, separators=(",", ":")))


if __name__ == "__main__":
    main()
