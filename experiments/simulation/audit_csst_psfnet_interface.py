#!/usr/bin/env python
"""Audit CSST-PSFNet compatibility without fabricating PSF ground truth.

The public implementation expects a multi-extension FITS file containing
STARS_XX, PSFS_XX and METADATA_XX HDUs and has no released checkpoint.  This
script records the available HDUs in a WPDC CSST-like image, then runs a
shape-only forward pass to verify the model interface.  It intentionally does
not train on the science image itself as a pseudo-label.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import torch
from astropy.io import fits


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo", type=Path, required=True, help="Local CSST_PSFNet checkout")
    p.add_argument("--fits", type=Path, required=True, help="AstroCFR CSST-like FITS image")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(); args.output.parent.mkdir(parents=True, exist_ok=True)
    # Some CSST integer images carry BSCALE/BZERO and cannot be read through
    # Astropy's memory-mapped scaling path.
    with fits.open(args.fits, memmap=False) as hdul:
        available = [h.name for h in hdul]
        image_shape = list(np.asarray(next(h.data for h in hdul if h.data is not None)).shape)
    required = ["STARS_12", "PSFS_12", "METADATA_12"]
    missing = [name for name in required if name not in available]
    sys.path.insert(0, str(args.repo))
    spec = importlib.util.spec_from_file_location("csst_psfnet_model", args.repo / "ImagePT2d_iccd_ImagePOS.py")
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    model = module.ImagePT_iccd_ImagePOS(device=str(device)).to(device).eval()
    with torch.no_grad():
        output, mu, log_var = model(torch.rand(1, 1, 32, 32, device=device), torch.tensor([12], device=device), torch.rand(1, 2, device=device))
    result = {
        "repository": str(args.repo), "license_file": str(args.repo / "LICENSE"),
        "available_hdus": available, "input_image_shape": image_shape,
        "required_training_hdus": required, "missing_required_hdus": missing,
        "checkpoint_files": [str(x) for x in args.repo.rglob("*.pth")],
        "model_parameters": int(sum(x.numel() for x in model.parameters())),
        "model_input_shape": [1, 1, 32, 32], "model_output_shape": list(output.shape),
        "device": str(device), "scientific_training_status": "blocked_without_ground_truth_psf_labels" if missing else "data_interface_available",
        "decision": "interface_only; do not report as a PSFNet accuracy baseline until labelled PSF FITS and a checkpoint/training protocol are supplied",
    }
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__": main()
