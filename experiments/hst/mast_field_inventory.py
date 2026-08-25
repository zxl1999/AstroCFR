#!/usr/bin/env python
"""Inventory candidate non-globular ACS/WFC programmes through the MAST API.

This queries metadata only; it downloads no FITS files and does not certify a
field for scientific comparison.  The resulting JSON is an auditable shortlist
for the FLC, catalogue-depth, and registration admission gates.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import requests


API = "https://mast.stsci.edu/api/v0/invoke"
PROGRAMS = {
    "phat_m31": {"proposal_id": "12055", "family": "M31 PHAT"},
    "phatter_m33": {"proposal_id": "14610", "family": "M33 PHATTER"},
    "http_tarantula": {"proposal_id": "12939", "family": "LMC Tarantula HTTP"},
    "angst": {"proposal_id": "10915", "family": "ANGST nearby galaxies"},
}


def query(proposal_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    pagesize = 500
    while True:
        request = {
            "service": "Mast.Caom.Filtered",
            "format": "json",
            "params": {
                "columns": "obsid,obs_collection,proposal_id,instrument_name,filters,target_name,t_exptime,dataproduct_type,calib_level",
                "filters": [
                    {"paramName": "proposal_id", "values": [proposal_id]},
                    {"paramName": "instrument_name", "values": ["ACS/WFC"]},
                ],
            },
            "pagesize": pagesize,
            "page": page,
        }
        response = requests.post(API, data={"request": json.dumps(request)}, timeout=120)
        response.raise_for_status()
        payload = response.json()
        batch = payload.get("data", [])
        rows.extend(batch)
        if len(batch) < pagesize:
            break
        page += 1
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    instruments = Counter(str(r.get("instrument_name")) for r in rows)
    filters = Counter(str(r.get("filters")) for r in rows)
    targets = Counter(str(r.get("target_name")) for r in rows)
    return {
        "rows": len(rows),
        "instrument_counts": dict(instruments),
        "filter_counts": dict(filters),
        "target_count": len(targets),
        "targets_top20": targets.most_common(20),
        "metadata_only": True,
        "admission_status": "pending FLC-product, catalogue-depth, and sub-pixel-registration audits",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("results/non_globular_field_inventory.json"))
    args = parser.parse_args()
    result: dict[str, Any] = {"protocol": {"api": API, "programs": PROGRAMS}}
    for key, info in PROGRAMS.items():
        rows = query(info["proposal_id"])
        result[key] = {"family": info["family"], "proposal_id": info["proposal_id"], "summary": summarize(rows), "rows": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
