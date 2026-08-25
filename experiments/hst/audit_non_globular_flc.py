#!/usr/bin/env python
"""Audit downloaded non-globular ACS/WFC FLC headers and hashes."""
from __future__ import annotations

import hashlib
import json
import csv
from collections import Counter
from pathlib import Path

from astropy.io import fits


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    root = Path("external/non_globular_fields")
    manifest_path = root / "download_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))["files"]
    rows = []
    errors = []
    for item in manifest:
        path = Path(item["local_path"])
        if not path.exists():
            errors.append({"file": str(path), "error": "missing"})
            continue
        if item.get("size") and path.stat().st_size != int(item["size"]):
            errors.append({"file": str(path), "error": "size mismatch"})
        digest = file_hash(path)
        if item.get("sha256") and digest != item["sha256"]:
            errors.append({"file": str(path), "error": "sha256 mismatch"})
        with fits.open(path, memmap=False, mode="readonly") as hdul:
            primary = hdul[0].header
            sci = hdul[1].header
            row = {
                "field_id": item["field_id"], "filename": item["product_filename"],
                "filter": primary.get("FILTER1"), "target": primary.get("TARGNAME"),
                "proposal_id": str(primary.get("PROPOSID")), "instrument": primary.get("INSTRUME"),
                "detector": primary.get("DETECTOR"), "date_obs": primary.get("DATE-OBS"),
                "exptime": primary.get("EXPTIME"), "ra_deg": primary.get("RA_TARG"),
                "dec_deg": primary.get("DEC_TARG"), "sci_shape": [sci.get("NAXIS2"), sci.get("NAXIS1")],
                "sha256": digest,
            }
            rows.append(row)
    summary = {
        "file_count": len(rows), "errors": errors,
        "field_counts": dict(Counter(r["field_id"] for r in rows)),
        "filter_counts": dict(Counter(r["filter"] for r in rows)),
        "instrument_detector": {f"{k[0]}/{k[1]}": v for k, v in Counter((r["instrument"], r["detector"]) for r in rows).items()},
        "all_files_cte_corrected_flc": all(r["filename"].lower().endswith("_flc.fits") for r in rows),
        "rows": rows,
    }
    out = Path("results/non_globular_flc_header_audit.json")
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest_out = Path("data/non_globular_flc_manifest.csv")
    with manifest_out.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["field_id", "filename", "source_url", "bytes", "sha256", "filter", "target", "proposal_id", "date_obs"])
        for item, row in zip(manifest, rows):
            uri = item["data_uri"]
            writer.writerow([row["field_id"], row["filename"], f"https://mast.stsci.edu/api/v0.1/Download/file?uri={uri}", item.get("size"), row["sha256"], row["filter"], row["target"], row["proposal_id"], row["date_obs"]])
    print(out)
    print(manifest_out)
    print(json.dumps({k: summary[k] for k in ("file_count", "errors", "field_counts", "filter_counts", "instrument_detector")}, indent=2))
    if errors: raise SystemExit(2)


if __name__ == "__main__": main()
