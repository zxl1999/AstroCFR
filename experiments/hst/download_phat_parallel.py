#!/usr/bin/env python
"""Download a PHAT Brick-21 catalogue using the verified range downloader."""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--field", choices=("f10", "f18"), required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--chunk-mb", type=float, default=1,
                        help="small chunks tolerate slow STScI archive connections")
    args = parser.parse_args()
    name = (f"hlsp_phat_hst_wfc3-uvis-acs-wfc-wfc3-ir_12055-m31-b21-{args.field}_"
            "f275w-f336w-f475w-f814w-f110w-f160w_v2_phot.fits.gz")
    url = "https://archive.stsci.edu/pub/hlsp/phat/brick21/" + name
    # Verified by HTTP HEAD on 2026-08-14; field-level archives differ in size.
    size = {"f10": 378699130, "f18": 365267504}[args.field]
    output = Path("external/reference_catalogs") / f"phat_b21_{args.field}_v2_phot.fits.gz"
    cmd = [sys.executable, str(HERE / "download_s3_parallel.py"), name, str(size),
           "--url", url, "--output", str(output), "--workers", str(args.workers),
           "--chunk-mb", str(args.chunk_mb)]
    raise SystemExit(subprocess.call(cmd))

if __name__ == "__main__":
    main()
