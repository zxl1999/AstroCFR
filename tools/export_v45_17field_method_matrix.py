#!/usr/bin/env python
"""Export the auditable 17-field by five-method results/status matrix.

The table deliberately keeps incompatible evidence tiers separate: CSST chips
have the registered AstroCFR result only, while the HST rows are measured
single-image external-catalogue comparisons.  Empty values mean unavailable,
not zero performance.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v45_17field_method_matrix.csv"
README = ROOT / "results" / "v45_17field_method_matrix.md"
METHODS = (
    ("dao", "DAOStarFinder"),
    ("sep", "SEP/SExtractor-style"),
    ("photutils_psf", "Photutils PSFPhotometry"),
    ("astrocfr_epsf", "AstroCFR ePSF + residual deblend"),
    ("astrocfr_photutils_hybrid", "AstroCFR+Photutils hybrid"),
)


def number(value, digits=3):
    return "" if value is None else f"{float(value):.{digits}f}"


def add(rows, *, tier, field, scene, method, status, result=None, metric="catalogue_recovery"):
    result = result or {}
    recovery = result.get(metric)
    rows.append({
        "evidence_tier": tier, "field": field, "scene": scene,
        "method": method, "status": status,
        "test_references": result.get("test_references", ""),
        "recovery_metric": metric if recovery is not None else "",
        "recovery_percent": "" if recovery is None else number(100 * recovery, 2),
        "astrometric_rms_mas": number(result.get("astrometric_rms_mas"), 3),
        "photometric_rms_mag": number(result.get("photometric_rms_mag"), 4),
        "runtime_s_per_mpix": number(result.get("runtime_s_per_mpix"), 3),
        "note": result.get("note", ""),
    })


def main():
    rows = []
    joint = json.loads((ROOT / "results/joint_csst_hst_m31_evidence/joint_evidence.json").read_text(encoding="utf-8"))
    csst = {x["field"]: x for x in joint["csst_registered"]}
    for chip in ("chip12", "chip13", "chip17", "chip18"):
        for key, label in METHODS:
            if key == "astrocfr_epsf":
                x = csst[chip]
                add(rows, tier="v44 CSST-like simulation", field=chip, scene="CSST-like simulated chip",
                    method=label, status="registered_result", result={
                        "catalogue_recovery": x["reference_recovery"],
                        "astrometric_rms_mas": x["position_rms_mas"],
                        "photometric_rms_mag": x["magnitude_rms_mag"],
                        "note": "Registered AstroCFR branch; no directly matched five-method runtime row archived.",
                    })
            else:
                add(rows, tier="v44 CSST-like simulation", field=chip, scene="CSST-like simulated chip",
                    method=label, status="not_archived_in_registered_protocol",
                    result={"note": "No directly aligned registered result for this method in the CSST evidence tier."})

    label_to_key = {label: key for key, label in METHODS}
    # v44 used the older WPDC name for the same recovery branch.
    label_to_key["WPDC ePSF + residual deblend"] = "astrocfr_epsf"
    for x in joint["hst_single_stack"]:
        key = label_to_key.get(x["method"])
        if key is None:
            continue  # RF/spatial-ePSF are retained elsewhere, not one of the requested five.
        add(rows, tier="v44 real HST/ACS", field=x["field"], scene="globular cluster",
            method=dict(METHODS)[key], status="complete", result={
                "test_references": x["test_references"], "catalogue_recovery": x["test_completeness"],
                "astrometric_rms_mas": x["position_rms_mas"], "photometric_rms_mag": x["magnitude_rms_mag"],
                "runtime_s_per_mpix": x["runtime_s_per_mpix"],
            })
    # The v44 main stack predates the hybrid ablation, so add a fifth row per
    # cluster from its separately archived matched-protocol benchmark.
    for field in ("ngc6397", "ngc6752", "ngc1851"):
        add(rows, tier="v44 real HST/ACS", field=field, scene="globular cluster",
            method="AstroCFR+Photutils hybrid", status="pending_hybrid_merge")
    hybrid = json.loads((ROOT / "results/hst_hybrid_wpdc_photutils/hybrid_summary.json").read_text(encoding="utf-8"))
    for x in hybrid["results"]:
        # Replace the generic v44 hybrid slot by the actual hybrid run.
        for row in rows:
            if row["field"] == x["cluster"] and row["method"] == "AstroCFR+Photutils hybrid":
                row.update({"status": "complete", "test_references": x["test_references"],
                            "recovery_metric": "catalogue_recovery", "recovery_percent": number(100*x["test_completeness"],2),
                            "astrometric_rms_mas": number(x["astrometric_rms_mas"],3),
                            "photometric_rms_mag": number(x["photometric_rms_mag"],4),
                            "runtime_s_per_mpix": number(x["runtime_s_per_mpix"],3), "note": ""})

    real = json.loads((ROOT / "results/real_field_4plus10/summary.json").read_text(encoding="utf-8"))
    real_results = real["results"]
    gr8_stack = ROOT / "results/real_field_4plus10/gr8_multiepoch/summary.json"
    if gr8_stack.exists():
        stack = json.loads(gr8_stack.read_text(encoding="utf-8"))
        # The older GR8 row used an ERR extension and is retained only in its
        # archived diagnostic; the matrix must show the valid SCI stack.
        real_results = [x for x in real_results if x.get("field") != "gr8"] + stack["results"]
    lookup = {(x.get("field"), x.get("method")): x for x in real_results if not x.get("error")}
    for ready in real["readiness"]:
        for key, label in METHODS:
            x = lookup.get((ready["field_id"], key))
            status = ("complete_multiepoch_protocol" if ready["field_id"] == "gr8" and x
                      else "complete" if x else "pending_real_input_or_catalogue")
            add(rows, tier="registered real 4+10", field=ready["field_id"], scene=ready["scene_class"],
                method=label, status=status, result=x)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader(); writer.writerows(rows)
    done = sum(r["status"] == "complete" for r in rows)
    README.write_text(
        "# 17-field × five-method evidence matrix\n\n"
        f"Rows: {len(rows)}; completed method rows: {done}. Blank metric cells are unavailable measurements, not zeros. "
        "Recovery is external-catalogue-conditioned for real fields and supplied-catalogue-conditioned for CSST.\n",
        encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
