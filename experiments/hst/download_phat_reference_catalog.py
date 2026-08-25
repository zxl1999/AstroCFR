#!/usr/bin/env python
"""Download and audit independent PHAT Brick-21 field catalogues.

The catalogue is never supplied to DOLPHOT or AstroCFR fitting.  It is an
external post-measurement reference candidate whose schema, sky coverage, and
F475W quality columns are audited before any matching experiment can proceed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import requests
from astropy.table import Table

FIELDS = ("f10", "f15", "f18")


def catalogue_url(field: str) -> str:
    return (
        "https://archive.stsci.edu/pub/hlsp/phat/brick21/"
        f"hlsp_phat_hst_wfc3-uvis-acs-wfc-wfc3-ir_12055-m31-b21-{field}_"
        "f275w-f336w-f475w-f814w-f110w-f160w_v2_phot.fits.gz"
    )


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--field", choices=FIELDS, default="f15")
    p.add_argument("--output", type=Path)
    p.add_argument("--audit", type=Path)
    args = p.parse_args()
    if args.output is None:
        args.output = Path(f"external/reference_catalogs/phat_b21_{args.field}_v2_phot.fits.gz")
    if args.audit is None:
        args.audit = Path(f"results/non_globular_runs/m31_b21_{args.field}/phat_catalogue_audit.json")
    url = catalogue_url(args.field)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.output.exists():
        with requests.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with args.output.open("wb") as f:
                for chunk in r.iter_content(1024 * 1024):
                    if chunk:
                        f.write(chunk)
    tab = Table.read(args.output)
    cols = list(tab.colnames)
    lower = {c.lower(): c for c in cols}
    ra = next((lower[k] for k in ("ra_j2000", "ra", "ra_deg") if k in lower), None)
    dec = next((lower[k] for k in ("dec_j2000", "dec", "dec_deg") if k in lower), None)
    # The field-level raw PHAT table stores F475W as the first combined
    # measurement block (COUNTS1...FLAG1), rather than verbose filter names.
    f475 = [c for c in cols if "475" in c.lower()] or [c for c in cols if c.endswith("1") or c in {"COUNTS1", "BG1"}]
    audit = {
        "field": args.field,
        "url": url,
        "path": str(args.output),
        "bytes": args.output.stat().st_size,
        "sha256": sha256(args.output),
        "rows": len(tab),
        "columns": cols,
        "f475w_columns": f475,
        "ra_column": ra,
        "dec_column": dec,
        "ra_range_deg": [float(min(tab[ra])), float(max(tab[ra]))] if ra else None,
        "dec_range_deg": [float(min(tab[dec])), float(max(tab[dec]))] if dec else None,
        "independence_statement": (
            "Downloaded PHAT HLSP v2 catalogue is held out from DOLPHOT and AstroCFR fitting; "
            "it may only be used after this audit as an external matching reference."
        ),
    }
    args.audit.parent.mkdir(parents=True, exist_ok=True)
    args.audit.write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps({k: audit[k] for k in ("bytes", "sha256", "rows", "f475w_columns", "ra_column", "dec_column")}, indent=2))


if __name__ == "__main__":
    main()
