#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GPU differentiable crowded-field refinement for image-only WPDC proposals.

This experiment is intentionally narrow.  WPDC supplies its established
image-only spatial-ePSF/residual-deblend proposals; only connected blend groups
are refined by a differentiable pixel-level forward model.  For each group the
free parameters are sub-pixel x/y, non-negative flux, and a shared background.
The PSF itself is the pre-built quadrant empirical PSF, not a catalogue-derived
template.  Catalogue positions/magnitudes are accessed only by the unchanged
held-out evaluator.

The branch is diagnostic until it is repeated on every HST field and on
artificial stars.  In particular, a GPU implementation is not assumed faster:
runtime and CUDA peak allocation are written to the output for each run.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from scipy.spatial import cKDTree


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def group_indices(xy: np.ndarray, radius: float = 7.0):
    """Connected components of a proposal-only neighbour graph."""
    tree = cKDTree(xy)
    parent = np.arange(len(xy))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in tree.query_pairs(radius):
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[rb] = ra
    groups = {}
    for i in range(len(xy)):
        groups.setdefault(root(i), []).append(i)
    return list(groups.values())


def make_template(psf: np.ndarray, xx: torch.Tensor, yy: torch.Tensor,
                  x: torch.Tensor, y: torch.Tensor) -> torch.Tensor:
    """Sample one empirical PSF per source with differentiable grid sampling."""
    size = psf.shape[0]
    half = (size - 1) / 2.0
    # grid_sample expects x/y normalized to [-1, 1] and returns N,H,W stamps.
    gx = 2.0 * (xx[None] - x[:, None, None] + half) / (size - 1) - 1.0
    gy = 2.0 * (yy[None] - y[:, None, None] + half) / (size - 1) - 1.0
    grid = torch.stack([gx, gy], dim=-1)
    src = psf[None, None].expand(len(x), -1, -1, -1)
    return F.grid_sample(src, grid, mode="bicubic", padding_mode="zeros",
                         align_corners=True)[:, 0]


def fit_group_torch(image: np.ndarray, grid, xy: np.ndarray, seed: np.ndarray,
                    ids: list[int], device: torch.device, max_steps: int = 45):
    """Fit an entire blend group with a Poisson/read-noise weighted forward model."""
    points = np.asarray(xy[ids], float)
    n = len(points)
    centre = np.mean(points, axis=0)
    spread = np.max(np.abs(points - centre)) if n else 0.0
    half = int(np.clip(np.ceil(spread) + 7, 7, 28))
    ix, iy = int(round(centre[0])), int(round(centre[1]))
    if ix-half < 0 or iy-half < 0 or ix+half >= image.shape[1] or iy+half >= image.shape[0]:
        return points, np.asarray(seed[ids], float), np.nan, False

    patch_np = np.asarray(image[iy-half:iy+half+1, ix-half:ix+half+1], np.float32)
    if not np.all(np.isfinite(patch_np)):
        return points, np.asarray(seed[ids], float), np.nan, False
    edge = np.r_[patch_np[0], patch_np[-1], patch_np[:, 0], patch_np[:, -1]]
    background = float(np.median(edge))
    # One quadrant PSF per local group.  Groups spanning a border are uncommon;
    # using the centre branch avoids any catalogue-informed model selection.
    qx = int(np.clip(centre[0] * 2 // image.shape[1], 0, 1))
    qy = int(np.clip(centre[1] * 2 // image.shape[0], 0, 1))
    psf = torch.as_tensor(grid[(qx, qy)][0], dtype=torch.float32, device=device)
    patch = torch.as_tensor(patch_np, dtype=torch.float32, device=device)
    yy, xx = torch.meshgrid(torch.arange(iy-half, iy+half+1, device=device, dtype=torch.float32),
                            torch.arange(ix-half, ix+half+1, device=device, dtype=torch.float32),
                            indexing="ij")
    base_xy = torch.as_tensor(points, dtype=torch.float32, device=device)
    # tanh keeps each solution within the documented 1.25-pixel local basin.
    offset = torch.zeros((n, 2), dtype=torch.float32, device=device, requires_grad=True)
    psf_centre = max(float(grid[(qx, qy)][0][grid[(qx, qy)][0].shape[0] // 2,
                                                grid[(qx, qy)][0].shape[1] // 2]), 1e-4)
    # The local image peak is approximately total_flux * PSF_centre.  Optimise
    # log flux, not flux itself: additive Adam steps on a 10^3--10^5 count
    # parameter cannot meaningfully alter a stellar amplitude in 20--50 steps.
    flux_seed = torch.as_tensor(np.maximum(seed[ids] / psf_centre, 1.0), dtype=torch.float32, device=device)
    log_flux = torch.log(flux_seed).detach().requires_grad_(True)
    bg = torch.tensor(background, dtype=torch.float32, device=device, requires_grad=True)
    params = [offset, log_flux, bg]
    optimiser = torch.optim.Adam(params, lr=0.06)
    best_loss = float("inf")
    best = None
    try:
        for _ in range(max_steps):
            optimiser.zero_grad(set_to_none=True)
            coords = base_xy + 1.25 * torch.tanh(offset)
            flux = torch.exp(torch.clamp(log_flux, min=-12.0, max=30.0))
            model = bg + torch.sum(flux[:, None, None] * make_template(psf, xx, yy, coords[:, 0], coords[:, 1]), dim=0)
            # A simple physical weighting: Poisson signal plus a conservative
            # 5 e- read-noise floor. Huber residual limits bright cosmic-ray
            # remnants without reference-label based rejection.
            scale = torch.sqrt(torch.clamp(model.detach(), min=0.0) + 25.0)
            loss = F.huber_loss((model - patch) / scale, torch.zeros_like(patch), delta=2.0)
            if not torch.isfinite(loss):
                break
            loss.backward()
            torch.nn.utils.clip_grad_norm_(params, 10.0)
            optimiser.step()
            value = float(loss.detach().cpu())
            if value < best_loss:
                best_loss = value
                best = (coords.detach().cpu().numpy(), flux.detach().cpu().numpy())
        if best is None:
            return points, np.asarray(seed[ids], float), np.nan, False
        return best[0], best[1], float(np.sqrt(best_loss)), True
    except RuntimeError:
        return points, np.asarray(seed[ids], float), np.nan, False


def run(args):
    repo = Path(args.work_dir).resolve()
    outdir = Path(args.output_dir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    hst_dir = repo / "experiments" / "hst"
    modules = repo / "src" / "wpdc"
    for directory in (str(modules), str(hst_dir)):
        if directory not in sys.path:
            sys.path.insert(0, directory)
    old = load(hst_dir / "hst_acsggct_benchmark.py", "hst_acsggct_benchmark")
    base = load(modules / "real_data_zero_shot_generalization.py", "real_data_zero_shot_generalization")
    epsf = load(modules / "hst_epsf_deblend_artificial_stars.py", "hst_epsf_deblend_artificial_stars")
    spatial = load(modules / "hst_spatial_epsf_joint_pilot.py", "hst_spatial_epsf_joint_pilot")
    # The public HST files are intentionally outside the Git checkout.  Earlier
    # workstation scripts used a local link beneath experiments/hst; discover
    # the shared CSST workspace copy when that link is absent.
    marker = "hlsp_acsggct_hst_acs-wfc_ngc6752_f606w_v2_img.fits"
    if not (old.DATA / marker).exists():
        located = list(repo.parent.rglob(marker))
        if len(located) != 1:
            raise RuntimeError(f"Cannot locate unique HST data marker {marker}: {len(located)} matches")
        old.DATA = located[0].parent
    device = torch.device(args.device if args.device else ("cuda" if torch.cuda.is_available() else "cpu"))
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
        torch.cuda.synchronize(device)

    image, cat = old.read_cluster(args.cluster)
    sub, rms = base.estimate_background(image)
    src = base.detect_sources(sub, rms, fwhm=2.2, threshold_sigma=3.0)
    initial = np.column_stack([np.asarray(src["xcentroid"], float), np.asarray(src["ycentroid"], float)])
    global_psf, _ = epsf.build_epsf(sub, src)
    candidates, _, _, added = epsf.residual_candidates(sub, rms, global_psf, initial)
    grid = spatial.build_quadrant_psfs(sub, src)
    seed = np.array([max(float(sub[int(round(y)), int(round(x))]), 1.0)
                     if 0 <= int(round(x)) < sub.shape[1] and 0 <= int(round(y)) < sub.shape[0] else 1.0
                     for x, y in candidates])
    groups = group_indices(candidates, radius=args.group_radius)
    fitted = np.empty_like(candidates)
    flux = np.empty(len(candidates))
    group_residual = np.full(len(candidates), np.nan)
    completed = skipped = 0
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    start = time.perf_counter()
    for ids in groups:
        if len(ids) == 1:
            # Preserve the validated WPDC spatial-ePSF path for isolated stars.
            x, y, f = spatial.fit_one_spatial(sub, grid, candidates[ids[0], 0], candidates[ids[0], 1], np.empty((0, 2)))
            fitted[ids] = [[x, y]]; flux[ids] = f
            continue
        coords, values, residual, success = fit_group_torch(sub, grid, candidates, seed, ids, device, args.steps)
        fitted[ids] = coords; flux[ids] = values; group_residual[ids] = residual
        completed += int(success); skipped += int(not success)
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    elapsed = time.perf_counter() - start

    x, y, _, quality, _ = old.catalog_subsets(cat)
    qxy = np.column_stack([x[quality], y[quality]])
    qmag = np.asarray(cat["Vvega"], float)[quality]
    part = old.ref_cells(qxy)
    global_fit = fitted + np.array([old.CROP_X0, old.CROP_Y0])
    test_det = old.adapt.cell_ids(fitted, 200) == 2
    test_ref = part == 2
    recalled, _ = old.one_to_one(global_fit[test_det], qxy[test_ref])
    dense_tree = cKDTree(qxy)
    density = np.array([len(dense_tree.query_ball_point(point, 10)) - 1 for point in qxy])
    dense = test_ref & (qmag <= 20) & (density >= 3)
    dense_match, _ = old.one_to_one(global_fit[test_det], qxy[dense])
    good_flux = np.isfinite(flux) & (flux > 0)
    metrics = {
        "cluster": args.cluster,
        "method": "wpdc_spatial_epsf_differentiable_group_refinement",
        "status": "exploratory_not_for_manuscript_until_multifield_and_injection_replication",
        "device": str(device),
        "torch_version": torch.__version__,
        "candidates": int(len(candidates)),
        "groups": int(len(groups)),
        "multi_source_groups": int(sum(len(group) > 1 for group in groups)),
        "differentiably_fitted_groups": completed,
        "fallback_groups": skipped,
        "residual_candidates_added": int(added),
        "test_references": int(np.sum(test_ref)),
        "test_completeness": float(np.sum(recalled) / max(np.sum(test_ref), 1)),
        "recall_v_le_20": float(old.one_to_one(global_fit[test_det], qxy[test_ref & (qmag <= 20)])[0].sum() / max(np.sum(test_ref & (qmag <= 20)), 1)),
        "high_density_v20_recall": float(np.sum(dense_match) / max(np.sum(dense), 1)),
        "high_density_v20_n": int(np.sum(dense)),
        "runtime_s": float(elapsed),
        "runtime_s_per_mpix": float(elapsed / (old.CROP_SIZE ** 2) * 1e6),
        "cuda_peak_allocated_mb": float(torch.cuda.max_memory_allocated(device) / 2**20) if device.type == "cuda" else None,
        "median_differentiable_group_loss_root": float(np.nanmedian(group_residual)),
    }
    metrics.update(old.measurement_metrics(global_fit, flux, good_flux, qxy, qmag, part))
    protocol = {
        "candidate_stage": "image-only WPDC ePSF + residual deblending",
        "measurement_stage": "quadrant empirical ePSF; differentiable local multi-source pixel forward model for blend groups; spatial-ePSF fallback for isolated proposals",
        "parameters_per_group": "x, y, non-negative flux for each source, one shared background",
        "objective": "Huberised Poisson plus 5 e- read-noise weighted pixel residual",
        "position_basin_px": 1.25,
        "association_radius_px": 2,
        "spatial_test_partition": 2,
        "catalogue_use": "evaluation only; affine and photometric zero-point calibration fit on non-test matches",
    }
    payload = {"protocol": protocol, "result": metrics}
    stem = f"{args.cluster}_differentiable_group"
    (outdir / f"{stem}_summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with (outdir / f"{stem}_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=sorted(metrics))
        writer.writeheader(); writer.writerow(metrics)
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, default=Path("."), help="AstroCFR repository root")
    parser.add_argument("--output-dir", type=Path, default=Path("results/hst_differentiable_group"))
    parser.add_argument("--cluster", default="ngc6752", choices=("ngc6397", "ngc6752", "ngc1851"))
    parser.add_argument("--device", default=None, help="torch device, defaults to CUDA when available")
    parser.add_argument("--steps", type=int, default=45)
    parser.add_argument("--group-radius", type=float, default=7.0)
    run(parser.parse_args())
