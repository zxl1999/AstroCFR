#!/usr/bin/env python
"""Export a compact real-star-field by method status/results matrix."""
from __future__ import annotations
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUMMARY = ROOT / "results/real_field_4plus10/summary.json"
OUT = ROOT / "results/real_field_4plus10/method_matrix.csv"
METHODS = ("dao", "sep", "photutils_psf", "astrocfr_epsf", "astrocfr_photutils_hybrid")
LABELS = {"dao": "DAOStarFinder", "sep": "SEP",
          "photutils_psf": "Photutils PSFPhotometry",
          "astrocfr_epsf": "AstroCFR ePSF",
          "astrocfr_photutils_hybrid": "AstroCFR+Photutils"}

def main():
    payload = json.loads(SUMMARY.read_text(encoding="utf-8"))
    results = payload["results"]
    # GR8's completed real multi-exposure run is deliberately stored outside
    # the historical summary until it has been reviewed.  Include it here as
    # an explicitly separate protocol row so the operational matrix exposes
    # its full measurement parameters without overwriting the old ERR-image
    # diagnostic row.
    gr8_stack = ROOT / "results/real_field_4plus10/gr8_multiepoch/summary.json"
    if gr8_stack.exists():
        stack = json.loads(gr8_stack.read_text(encoding="utf-8"))
        results = [x for x in results if x.get("field") != "gr8"] + stack["results"]
    output = []
    for field in payload["readiness"]:
        fid = field["field_id"]
        for method in METHODS:
            row = next((x for x in results if x.get("field") == fid and x.get("method") == method), None)
            if row is None:
                status, recovery = "pending", None
            elif row.get("error"):
                status, recovery = "error", None
            elif fid == "gr8" and row.get("astrometric_rms_mas") is not None:
                status, recovery = "complete_multiepoch_protocol", row.get("catalogue_recovery")
            elif field.get("manuscript_admitted"):
                status, recovery = "admitted", row.get("catalogue_recovery")
            elif field.get("all_methods_complete"):
                status, recovery = "run_complete_metric_pending", row.get("catalogue_recovery")
            else:
                status, recovery = "run_complete", row.get("catalogue_recovery")
            output.append({"slot": field["evidence_slot"], "field_id": fid,
                           "scene_class": field["scene_class"], "method": LABELS[method],
                           "status": status,
                           "catalogue_recovery": "" if recovery is None else f"{100*recovery:.2f}%",
                           "test_references": "" if row is None else row.get("test_references", ""),
                           "astrometric_rms_mas": "" if row is None or row.get("astrometric_rms_mas") is None else f"{row['astrometric_rms_mas']:.2f}",
                           "photometric_rms_mag": "" if row is None or row.get("photometric_rms_mag") is None else f"{row['photometric_rms_mag']:.3f}",
                           "runtime_s_per_mpix": "" if row is None or row.get("runtime_s_per_mpix") is None else f"{row['runtime_s_per_mpix']:.2f}"})
    with OUT.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=output[0].keys())
        writer.writeheader(); writer.writerows(output)
    print(OUT)

if __name__ == "__main__":
    main()
