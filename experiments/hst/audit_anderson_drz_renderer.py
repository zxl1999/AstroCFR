#!/usr/bin/env python
"""Audit the independent Anderson-to-DRZ artificial-star renderer.

This is a geometry/normalization audit, not a recovery experiment.  It renders
one representative position for every ACS extension x density stratum and
checks the output-pixel centroid against the requested DRZ coordinate.  The
reported Gaussian-equivalent FWHM is derived from the second-moment covariance
of the normalized 25 x 25 stamp and is intended only as a gross shape check.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import least_squares


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src" / "wpdc"))
from anderson_drz_injection import AndersonDRZRenderer


SCENE = ROOT / "results/non_globular_runs/m31_b21_f15/matched_coordinate_scene/mapped_fake_stars.csv"
FIELD = ROOT / "external/non_globular_fields/m31_b21_f15"
DRZ = FIELD / "phat_f475w_drz.fits"
FLC_NAMES = ("jbex18u6q_flc.fits", "jbex18u9q_flc.fits", "jbex18ucq_flc.fits")
STDPSF = ROOT / "external/reference_catalogs/STDPSF_ACSWFC_F475W.fits"
OUT = ROOT / "results/non_globular_runs/m31_b21_f15/matched_coordinate_scene/anderson_psf_audit"
DRZ_BLUR_SIGMA_PX = 0.55


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def representative_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Choose a deterministic, spatially central row in each declared stratum."""
    selected = []
    for extension in ("1", "2"):
        for density in ("low", "high"):
            group = [r for r in rows if r["extension"] == extension and r["density_band"] == density]
            xy = np.array([[float(r["drz_x"]), float(r["drz_y"])] for r in group])
            centre = np.median(xy, axis=0)
            index = int(np.argmin(np.sum((xy - centre) ** 2, axis=1)))
            selected.append(group[index])
    return selected


def metrics(stamp: np.ndarray, x: float, y: float, half: int) -> dict[str, float]:
    # Coordinates in the stamp's output-pixel system.  Integer DRZ coordinates
    # occupy integer array offsets because both WCS calls use FITS origin=1.
    expected_x = half + x - round(x)
    expected_y = half + y - round(y)
    yy, xx = np.indices(stamp.shape, dtype=float)
    total = float(stamp.sum())
    cx = float(np.sum(stamp * xx) / total)
    cy = float(np.sum(stamp * yy) / total)
    dx, dy = cx - expected_x, cy - expected_y
    var_x = float(np.sum(stamp * (xx - cx) ** 2) / total)
    var_y = float(np.sum(stamp * (yy - cy) ** 2) / total)
    cov_xy = float(np.sum(stamp * (xx - cx) * (yy - cy)) / total)
    eigenvalues = np.linalg.eigvalsh([[var_x, cov_xy], [cov_xy, var_y]])
    core = (np.abs(xx - cx) <= 3) & (np.abs(yy - cy) <= 3)
    core_x, core_y, core_z = xx[core], yy[core], stamp[core]
    def residual(parameters):
        amplitude, x0, y0, sigma_x, sigma_y, background = parameters
        model = amplitude * np.exp(-0.5*((core_x-x0)/sigma_x)**2-0.5*((core_y-y0)/sigma_y)**2)+background
        return model-core_z
    fit = least_squares(residual, [stamp.max(), cx, cy, 0.95, 0.95, 0.0],
                        bounds=([0,cx-2,cy-2,0.2,0.2,-1],[1,cx+2,cy+2,3,3,1])).x
    return {
        "sum": total,
        "expected_centroid_x": expected_x,
        "expected_centroid_y": expected_y,
        "measured_centroid_x": cx,
        "measured_centroid_y": cy,
        "centroid_dx_px": dx,
        "centroid_dy_px": dy,
        "centroid_radial_offset_px": float(math.hypot(dx, dy)),
        "fwhm_minor_px_second_moment": float(2.354820045 * math.sqrt(max(eigenvalues[0], 0))),
        "fwhm_major_px_second_moment": float(2.354820045 * math.sqrt(max(eigenvalues[1], 0))),
        "fwhm_x_px_gaussian_core": float(2.354820045*fit[3]),
        "fwhm_y_px_gaussian_core": float(2.354820045*fit[4]),
        "negative_pixel_count": int(np.count_nonzero(stamp < 0)),
        "finite": bool(np.isfinite(stamp).all()),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(SCENE.open(encoding="utf-8")))
    chosen = representative_rows(rows)
    renderer = AndersonDRZRenderer(STDPSF, DRZ, [FIELD / "flc" / name for name in FLC_NAMES], drz_blur_sigma_px=DRZ_BLUR_SIGMA_PX)
    audited = []
    stamps = []
    try:
        for row in chosen:
            x, y = float(row["drz_x"]), float(row["drz_y"])
            stamp = renderer.unit_patch(x, y, int(row["extension"]), half=12)
            item = {
                "fake_id": int(row["fake_id"]),
                "logical_extension": int(row["extension"]),
                "density_band": row["density_band"],
                "drz_x": x,
                "drz_y": y,
                **metrics(stamp, x, y, 12),
            }
            audited.append(item)
            stamps.append(stamp)
    finally:
        renderer.close()

    max_offset = max(r["centroid_radial_offset_px"] for r in audited)
    payload = {
        "purpose": "geometry, normalization, and gross-shape audit only; not scientific recovery evidence",
        "standard_psf": str(STDPSF),
        "standard_psf_bytes": STDPSF.stat().st_size,
        "standard_psf_sha256": sha256(STDPSF),
        "renderer": "three FLC WCS projections averaged on the PHAT F475W DRZ output grid and convolved with a fixed Gaussian sigma=0.55-px output kernel",
        "drz_blur_sigma_px": DRZ_BLUR_SIGMA_PX,
        "target_image_fwhm_px": 2.2,
        "limitations": "the fixed broadening matches the measured DRZ scale but does not reproduce the full AstroDrizzle kernel or correlated-noise process",
        "selection": "one deterministic spatially central position per logical-extension x density stratum",
        "acceptance_criteria": {
            "abs_sum_minus_one_max": 1e-10,
            "centroid_radial_offset_px_max": 0.20,
            "all_finite": True,
            "negative_pixel_count": 0,
        },
        "passed": bool(
            all(abs(r["sum"] - 1.0) <= 1e-10 for r in audited)
            and max_offset <= 0.20
            and all(r["finite"] and r["negative_pixel_count"] == 0 for r in audited)
        ),
        "maximum_centroid_radial_offset_px": max_offset,
        "positions": audited,
    }
    (OUT / "anderson_drz_psf_audit.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    fig, axes = plt.subplots(2, 2, figsize=(7.2, 6.4), constrained_layout=True)
    for ax, stamp, item in zip(axes.ravel(), stamps, audited):
        image = ax.imshow(stamp, origin="lower", cmap="viridis", interpolation="nearest")
        ax.plot(item["expected_centroid_x"], item["expected_centroid_y"], "+", color="white", ms=9, mew=1.3, label="requested")
        ax.plot(item["measured_centroid_x"], item["measured_centroid_y"], "x", color="#ff5a5f", ms=7, mew=1.3, label="centroid")
        ax.set_title(
            f"ext {item['logical_extension']}, {item['density_band']}\n"
            f"offset={item['centroid_radial_offset_px']:.3f} px, "
            f"core FWHM={item['fwhm_x_px_gaussian_core']:.2f}x{item['fwhm_y_px_gaussian_core']:.2f} px",
            fontsize=9,
        )
        ax.set_xlim(7, 17); ax.set_ylim(7, 17)
        fig.colorbar(image, ax=ax, fraction=0.046, pad=0.03)
    axes[0, 0].legend(frameon=False, fontsize=7, loc="upper right")
    fig.suptitle("Official Anderson F475W PSF projected through three FLC WCS solutions", fontsize=11)
    fig.savefig(OUT / "anderson_drz_psf_contact_sheet.png", dpi=250, bbox_inches="tight")
    plt.close(fig)
    print(json.dumps(payload, indent=2))
    if not payload["passed"]:
        raise SystemExit("Anderson-to-DRZ renderer failed its registered geometry audit")


if __name__ == "__main__":
    main()
