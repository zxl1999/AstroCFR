#!/usr/bin/env python
"""Summarise the completed 11-field HST runtime and magnitude strata."""
import csv, json, statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "results" / "acsggct_all11_baselines" / "hst_unified_baseline_summary.json"
OUT = ROOT / "results" / "acsggct_all11_baselines"
FIELDS = ("ngc2808", "ngc5286", "ngc6388", "ngc6441", "ngc0104", "ngc0362", "ngc6093", "ngc6624", "ngc6397", "ngc6752", "ngc1851")
METHODS = ("dao", "sep", "photutils_psf", "wpdc_epsf_deblend", "wpdc_spatial_epsf_joint")

def main():
    rows = json.loads(SRC.read_text(encoding="utf-8"))["results"]
    by = {(r["cluster"], r["method"]): r for r in rows}
    summary = {m: {k: statistics.median(by[(f, m)][k] for f in FIELDS) for k in ("runtime_s_per_mpix", "runtime_s", "recall_v_le_18", "recall_v_le_20", "recall_v_le_22")} for m in METHODS}
    summary["metadata"] = {"fields": list(FIELDS), "area_pixels": 1200 * 1200, "hardware_scope": "CPU crop timing; no GPU or full-detector extrapolation"}
    (OUT / "hst_runtime_magnitude_stratified_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with (OUT / "hst_runtime_magnitude_stratified_summary.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["method", "runtime_s_per_mpix_median", "runtime_s_median", "recall_v_le_18_median", "recall_v_le_20_median", "recall_v_le_22_median"])
        for m in METHODS:
            s = summary[m]; w.writerow([m, s["runtime_s_per_mpix"], s["runtime_s"], s["recall_v_le_18"], s["recall_v_le_20"], s["recall_v_le_22"]])
    print(OUT / "hst_runtime_magnitude_stratified_summary.json")

if __name__ == "__main__":
    main()
