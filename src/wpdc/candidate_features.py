"""Stable candidate-feature and PSF-estimation interface for AstroCFR.

These functions are extracted from the historical monolithic challenge script
and preserve the registered 17-feature ordering used by the real-domain and
HST experiments.
"""
from __future__ import annotations

import numpy as np
from photutils.aperture import CircularAperture, aperture_photometry
from scipy.optimize import curve_fit
from scipy.spatial import cKDTree

FEATURE_NAMES = (
    "sharpness", "roundness1", "roundness2", "peak", "log10_flux_r5", "snr",
    "concentration_r3_r5", "concentration_r5_r8", "concentration_r3_r8",
    "local_background_p20", "local_background_std", "nearest_neighbour_px",
    "neighbours_r10", "normalized_distance_to_center", "ellipticity",
    "distance_to_brightest_px", "peak_contrast",
)


def _xy_columns(table):
    if "xcentroid" in table.colnames:
        return "xcentroid", "ycentroid"
    if "x_centroid" in table.colnames:
        return "x_centroid", "y_centroid"
    return table.colnames[0], table.colnames[1]


def _xy(table):
    xcol, ycol = _xy_columns(table)
    return np.asarray(table[xcol], float), np.asarray(table[ycol], float)


def _isolated_bright_indices(sources, bkg_rms, min_snr=25.0, min_sep=12.0,
                             max_sources=40, random_seed=42):
    del random_seed
    if not len(sources):
        return np.array([], dtype=int)
    x, y = _xy(sources)
    peak = np.asarray(sources["peak"], float) if "peak" in sources.colnames else np.ones(len(sources))
    snr = peak / max(float(bkg_rms), 1.0)
    order = np.where(np.isfinite(snr) & (snr >= min_snr))[0]
    order = order[np.argsort(-snr[order])]
    tree = cKDTree(np.c_[x, y])

    def select(separation):
        kept = []
        for index in order:
            distance, _ = tree.query([x[index], y[index]], k=min(8, len(sources)))
            if not kept or np.all(np.asarray(distance[1:]) >= separation):
                kept.append(index)
            if len(kept) >= max_sources:
                break
        return kept

    kept = select(min_sep)
    if len(kept) < 5:
        kept = select(max(6.0, min_sep * 0.5))
    return np.asarray(kept, dtype=int)


def estimate_psf_fwhm(data_sub, sources, bkg_rms, min_snr=25.0, max_sources=40,
                      cutout_half=9, random_seed=42):
    """Estimate representative PSF FWHM from bright isolated sources."""
    if not len(sources):
        return 3.0
    x, y = _xy(sources)
    peak = np.asarray(sources["peak"], float) if "peak" in sources.colnames else np.ones(len(sources))
    snr = peak / max(float(bkg_rms), 1.0)
    selected = _isolated_bright_indices(sources, bkg_rms, min_snr, 8.0,
                                        max_sources, random_seed)
    if not len(selected):
        selected = np.where(np.isfinite(snr) & (snr >= min_snr))[0]
    if not len(selected):
        return 3.0
    rng = np.random.default_rng(random_seed)
    if len(selected) > max_sources:
        selected = rng.choice(selected, max_sources, replace=False)

    def fit_profile(radius, profile):
        finite = np.isfinite(radius) & np.isfinite(profile)
        radius, profile = radius[finite], profile[finite]
        if len(radius) < 8:
            return np.nan
        profile -= np.nanmedian(profile[-max(3, len(profile) // 4):])
        peak_value = np.nanmax(profile)
        if not np.isfinite(peak_value) or peak_value <= 0:
            return np.nan
        keep = profile > 0.08 * peak_value
        if keep.sum() < 6:
            keep = np.arange(len(profile)) < max(8, len(profile) // 2)
        rfit, pfit = radius[keep], profile[keep]
        try:
            def model(r, amplitude, sigma, constant):
                return amplitude * np.exp(-(r ** 2) / (2 * sigma ** 2)) + constant
            weights = np.maximum(pfit, 0)
            sigma0 = np.clip(np.sqrt(np.sum(rfit ** 2 * weights) /
                                      (np.sum(weights) + 1e-9)), 0.6, 6.0)
            initial = [max(np.nanmax(pfit) - np.nanmin(pfit), 1.0), sigma0, np.nanmin(pfit)]
            fitted, _ = curve_fit(model, rfit, pfit, p0=initial,
                                  bounds=([0, .35, -np.inf], [np.inf, 10, np.inf]),
                                  maxfev=5000)
            return float(2.354820045 * fitted[1])
        except Exception:
            weights = np.clip(pfit, 0, None)
            if weights.sum() <= 0:
                return np.nan
            sigma = np.sqrt(np.sum(rfit ** 2 * weights) / (weights.sum() + 1e-9))
            return float(2.354820045 * np.clip(sigma, .35, 10))

    bins = np.linspace(0, cutout_half, max(12, cutout_half * 4) + 1)
    centers = .5 * (bins[:-1] + bins[1:])
    ny, nx = data_sub.shape
    estimates = []
    for index in selected:
        xc, yc = int(round(x[index])), int(round(y[index]))
        x0, x1 = max(0, xc-cutout_half), min(nx, xc+cutout_half+1)
        y0, y1 = max(0, yc-cutout_half), min(ny, yc+cutout_half+1)
        if x1-x0 < 7 or y1-y0 < 7:
            continue
        patch = data_sub[y0:y1, x0:x1]
        yy, xx = np.mgrid[y0:y1, x0:x1]
        radius = np.hypot(xx-x[index], yy-y[index])
        profile = []
        for lower, upper in zip(bins[:-1], bins[1:]):
            values = patch[(radius >= lower) & (radius < upper)]
            values = values[np.isfinite(values)]
            profile.append(np.nanmedian(values) if len(values) else np.nan)
        estimate = fit_profile(centers, np.asarray(profile, float))
        if np.isfinite(estimate):
            estimates.append(estimate)
    return float(np.clip(np.nanmedian(estimates), 1.2, 8.0)) if estimates else 3.0


def _extract_clf_features(sources, data_sub, bkg_rms):
    """Return the registered 17 features and radius-five aperture flux."""
    xcol, ycol = _xy_columns(sources)
    x, y = np.asarray(sources[xcol], float), np.asarray(sources[ycol], float)
    n = len(sources)
    if n == 0:
        return np.empty((0, len(FEATURE_NAMES))), np.empty(0)
    ny, nx = data_sub.shape
    sharp = np.asarray(sources["sharpness"], float)
    round1 = np.asarray(sources["roundness1"], float)
    round2 = np.asarray(sources["roundness2"], float)
    peak = np.asarray(sources["peak"], float)
    positions = np.c_[x, y]
    flux = {radius: aperture_photometry(data_sub, CircularAperture(positions, r=radius))
            ["aperture_sum"].data.astype(float) for radius in (3, 5, 8)}
    f3, f5, f8 = (np.maximum(flux[radius], 1.0) for radius in (3, 5, 8))
    xi, yi = np.clip(x.astype(int), 0, nx-1), np.clip(y.astype(int), 0, ny-1)
    local_bkg, local_std = np.zeros(n), np.zeros(n)
    for index in range(n):
        patch = data_sub[max(0, yi[index]-8):min(ny, yi[index]+9),
                         max(0, xi[index]-8):min(nx, xi[index]+9)]
        if patch.size:
            local_bkg[index], local_std[index] = np.percentile(patch, 20), np.std(patch)
    tree = cKDTree(positions)
    nearest = tree.query(positions, k=2)[0][:, 1] if n > 1 else np.array([np.inf])
    neighbours = np.array([len(tree.query_ball_point(point, 10))-1 for point in positions], float)
    brightest = positions[np.argmax(peak)]
    features = np.column_stack([
        sharp, round1, round2, peak, np.log10(f5), peak/max(float(bkg_rms), 1.0),
        f3/f5, f5/f8, f3/f8, local_bkg, local_std, nearest, neighbours,
        np.hypot(x-nx/2, y-ny/2)/max(nx, ny), np.hypot(round1, round2),
        np.hypot(x-brightest[0], y-brightest[1]), peak/np.maximum(local_std, 1.0),
    ])
    return np.nan_to_num(features, nan=0.0, posinf=1e6, neginf=-1e6), flux[5]
