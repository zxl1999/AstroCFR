#!/usr/bin/env python
"""External-catalogue consistency audit for the accepted M31 DOLPHOT run.

This evaluates a completed DOLPHOT catalogue against the public PHAT field
catalogue only after the fit has finished.  It reports a *catalogue-match lower
bound*, never blind purity or completeness: PHAT is itself a finite,
multi-band DOLPHOT-derived catalogue, not an exhaustive injected truth scene.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS
from astropy import units as u
from scipy.spatial import cKDTree


def quality(raw: np.ndarray) -> np.ndarray:
    """Image-only DOLPHOT science selection, declared before PHAT matching."""
    mag, snr, sharp, crowd, obj, flag = raw[:, 16], raw[:, 5], raw[:, 6], raw[:, 9], raw[:, 10], raw[:, 24]
    return np.isfinite(mag) & (mag < 90) & (obj <= 2) & (snr >= 5) & (np.abs(sharp) <= 0.3) & (crowd <= 0.5) & (flag == 0)


def to_sky(raw: np.ndarray, reference_flc: Path) -> tuple[np.ndarray, np.ndarray]:
    """Transform DOLPHOT reference-frame x/y through the matching SCI WCS."""
    ra = np.full(len(raw), np.nan)
    dec = np.full(len(raw), np.nan)
    with fits.open(reference_flc, memmap=True) as hdul:
        # DOLPHOT extension 1/2 corresponds to ACS SCI extensions 1/2 here.
        for ext in (1, 2):
            sel = raw[:, 0].astype(int) == ext
            if not sel.any():
                continue
            w = WCS(hdul[ext].header, fobj=hdul)
            r, d = w.all_pix2world(raw[sel, 2], raw[sel, 3], 0)
            ra[sel], dec[sel] = r, d
    return ra, dec


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--dolphot', type=Path, required=True)
    p.add_argument('--reference-flc', type=Path, required=True)
    p.add_argument('--phat', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--radius-arcsec', type=float, default=0.10)
    args = p.parse_args()
    raw = np.loadtxt(args.dolphot)
    keep = quality(raw)
    raw = raw[keep]
    dra, ddec = to_sky(raw, args.reference_flc)
    finite = np.isfinite(dra) & np.isfinite(ddec)
    raw, dra, ddec = raw[finite], dra[finite], ddec[finite]
    phat = Table.read(args.phat)
    # F475W is block 1 in the raw PHAT field catalogue.  Keep only valid,
    # measured sources; this selection is declared independently of DOLPHOT.
    pmag = np.asarray(phat['MAG1_VEGA'], float)
    perr = np.asarray(phat['MAG1_ERR'], float)
    psnr = np.asarray(phat['SNR1'], float)
    pflag = np.asarray(phat['FLAG1'], int)
    pkeep = np.isfinite(pmag) & (pmag < 90) & (perr < 0.2) & (psnr >= 5) & (pflag == 0)
    pra = np.asarray(phat['RA_J2000'][pkeep], float)
    pdec = np.asarray(phat['DEC_J2000'][pkeep], float)
    # Tangent-plane KD tree is sufficient within one ACS field.
    dec0 = np.deg2rad(np.nanmedian(pdec))
    tree = cKDTree(np.column_stack([pra * np.cos(dec0) * 3600, pdec * 3600]))
    dist, ind = tree.query(np.column_stack([dra * np.cos(dec0) * 3600, ddec * 3600]), k=1)
    match = dist <= args.radius_arcsec
    dx_mas = (dra[match] - pra[ind[match]]) * np.cos(dec0) * 3.6e6
    dy_mas = (ddec[match] - pdec[ind[match]]) * 3.6e6
    dmag = raw[match, 16] - pmag[pkeep][ind[match]]
    # Robust residual summaries prevent a few poor WCS matches from being read
    # as photometric performance.
    def rms(v: np.ndarray) -> tuple[float | None, int]:
        if len(v) < 10:
            return None, int(len(v))
        med = np.median(v); mad = 1.4826 * np.median(np.abs(v - med)); good = np.abs(v - med) <= max(3 * mad, 1e-6)
        return float(np.sqrt(np.mean((v[good] - np.mean(v[good])) ** 2))), int(good.sum())
    pos = np.hypot(dx_mas, dy_mas)
    pos_rms, pos_n = rms(pos)
    mag_rms, mag_n = rms(dmag)
    summary = {
        'status': 'external_catalogue_consistency_only',
        'dolphot_input_rows': int(len(keep)), 'dolphot_quality_rows': int(len(raw)),
        'phat_rows': int(len(phat)), 'phat_quality_rows': int(pkeep.sum()),
        'match_radius_arcsec': args.radius_arcsec, 'matched_rows': int(match.sum()),
        'dolphot_match_fraction_lower_bound': float(match.mean()),
        'position_radial_rms_mas_after_robust_clip': pos_rms, 'position_inliers': pos_n,
        'f475w_difference_dolphot_minus_phat_mag_median': float(np.median(dmag)) if len(dmag) else None,
        'f475w_difference_rms_mag_after_robust_clip': mag_rms, 'photometric_inliers': mag_n,
        'limitations': ('PHAT v2 field photometry is a finite external catalogue produced with DOLPHOT; '
                        'these are catalogue-consistency values, not blind purity, completeness, or artificial-star recovery.'),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix('.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    with args.out.with_suffix('.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=summary.keys()); w.writeheader(); w.writerow(summary)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
