#!/usr/bin/env python
"""Build the final 11 ACSGGCT + 4 CSST all-method evidence matrix.

This exporter never treats a missing or input-incompatible run as a zero.  The
HST rows use the v45 central-crop / held-out-spatial-partition protocol; the
CSST rows preserve the separately registered full-frame simulation audit.
"""
from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results"
MATRIX = OUT / "acsggct11_csst4_all_methods_matrix.csv"
SUMMARY = OUT / "acsggct11_csst4_method_summary.csv"
REGISTRY = OUT / "acsggct11_csst4_method_registry.csv"
PAIRWISE = OUT / "acsggct11_spatial_vs_photutils_by_field.csv"
README = OUT / "acsggct11_csst4_all_methods_matrix.md"

HST_FIELDS = ("ngc2808", "ngc5286", "ngc6388", "ngc6441", "ngc0104",
              "ngc0362", "ngc6093", "ngc6624", "ngc6397", "ngc6752", "ngc1851")
CSST_FIELDS = ("chip12", "chip13", "chip17", "chip18")

METHODS = (
    ("dao", "DAOStarFinder", "single-image baseline"),
    ("sep", "SEP/SExtractor-style", "single-image baseline"),
    ("photutils_psf", "Photutils PSFPhotometry", "single-image baseline"),
    ("astrocfr_rf", "AstroCFR RF candidate branch", "v45 branch"),
    ("astrocfr_epsf_deblend", "AstroCFR ePSF + residual deblend", "v45 branch"),
    ("astrocfr_spatial_epsf_joint", "AstroCFR spatial-ePSF joint", "v45 branch"),
    ("astrocfr_photutils_hybrid", "AstroCFR+Photutils hybrid", "v45 ablation"),
    ("global_epsf_joint", "Global empirical ePSF + neighbour joint", "literature mapping: effective-PSF ablation (Libralato et al. 2024)"),
    ("three_gaussian_dpsf_joint", "Three-Gaussian dPSF + neighbour joint", "literature mapping: discrete-PSF approximation (Nie et al. 2025)"),
    ("spatial_epsf_joint", "Spatial empirical ePSF + neighbour joint", "literature-mapped spatial ePSF control"),
    ("dolphot", "DOLPHOT/ALLFRAME-class multi-exposure", "external pipeline"),
    ("crowdsource", "crowdsource joint multiband", "external pipeline"),
    ("euclid_vvv", "Euclid/VVV full workflow", "external pipeline"),
    ("csst_psfnet", "CSST-PSFNet", "external PSF model"),
)
METHOD_LABEL = {key: label for key, label, _ in METHODS}
METHOD_GROUP = {key: group for key, _, group in METHODS}


def fmt(value, digits=6):
    return "" if value is None else f"{float(value):.{digits}f}"


def standard_row(*, tier, field, method_id, status, protocol, source, result=None, note=""):
    result = result or {}
    recall = result.get("recovery")
    return {
        "evidence_tier": tier,
        "field": field,
        "method_id": method_id,
        "method": METHOD_LABEL[method_id],
        "method_group": METHOD_GROUP[method_id],
        "status": status,
        "protocol": protocol,
        "source": source,
        "test_references": result.get("test_references", ""),
        "recovery_metric": result.get("recovery_metric", ""),
        "recovery_percent": fmt(None if recall is None else 100 * recall, 3),
        "high_density_v20_recall_percent": fmt(result.get("high_density_recall") * 100 if result.get("high_density_recall") is not None else None, 3),
        "high_density_v20_n": result.get("high_density_n", ""),
        "astrometric_rms_mas": fmt(result.get("astrometric_rms_mas"), 4),
        "photometric_rms_mag": fmt(result.get("photometric_rms_mag"), 5),
        "runtime_s": fmt(result.get("runtime_s"), 3),
        "runtime_s_per_mpix": fmt(result.get("runtime_s_per_mpix"), 3),
        "note": note,
    }


def add_incompatible(rows, field, method_id, tier):
    notes = {
        "dolphot": "Requires homogeneous calibrated multi-exposure inputs and native artificial-star scenes; a stacked single F606W image is not an input-equivalent DOLPHOT/ALLFRAME experiment.",
        "crowdsource": "Requires matched multi-band images and a joint-band source model; this matrix uses a single F606W stack or a CSST chip.",
        "euclid_vvv": "Requires instrument-specific distortion, dithers and/or multi-epoch local transformations unavailable in this matched protocol.",
        "csst_psfnet": "Requires labelled 32x32 stellar stamps, PSF labels and a checkpoint/training protocol; available CSST FITS lacks the required labelled extensions.",
    }
    rows.append(standard_row(tier=tier, field=field, method_id=method_id,
                             status="input_incompatible_not_ranked", protocol="not run", source="interface/scope audit", note=notes[method_id]))


def main():
    rows = []
    baseline = json.loads((OUT / "acsggct_all11_baselines" / "hst_unified_baseline_summary.json").read_text(encoding="utf-8"))
    for x in baseline["results"]:
        key = {"wpdc_epsf_deblend": "astrocfr_epsf_deblend",
               "wpdc_spatial_epsf_joint": "astrocfr_spatial_epsf_joint"}.get(x["method"], x["method"])
        rows.append(standard_row(
            tier="HST/ACS single-F606W controlled", field=x["cluster"], method_id=key,
            status="complete", protocol="central 1200x1200 crop; held-out spatial test partition; 2-pixel one-to-one association",
            source="results/acsggct_all11_baselines/hst_unified_baseline_summary.json",
            result={"test_references": x.get("test_references"), "recovery": x.get("test_completeness"),
                    "recovery_metric": "held_out_catalogue_recovery", "high_density_recall": x.get("high_density_v20_recall"),
                    "high_density_n": x.get("high_density_v20_n"), "astrometric_rms_mas": x.get("astrometric_rms_mas"),
                    "photometric_rms_mag": x.get("photometric_rms_mag"), "runtime_s": x.get("runtime_s"),
                    "runtime_s_per_mpix": x.get("runtime_s_per_mpix")},
            note="Image-only fitting; the reference catalogue is used only for held-out evaluation/calibration."))

    literature = json.loads((OUT / "hst_literature_method_benchmark_all11" / "summary.json").read_text(encoding="utf-8"))
    for x in literature["results"]:
        rows.append(standard_row(
            tier="HST/ACS single-F606W controlled", field=x["cluster"], method_id=x["method"],
            status="complete", protocol="central 1200x1200 crop; held-out dense subset; 2-pixel one-to-one association",
            source="results/hst_literature_method_benchmark_all11/summary.json",
            result={"test_references": x.get("dense_reference_test_n"),
                    "recovery": (x.get("matched_test_dense_n", 0) / x["dense_reference_test_n"]) if x.get("dense_reference_test_n") else None,
                    "recovery_metric": "held_out_dense_catalogue_recovery", "high_density_recall": None,
                    "high_density_n": x.get("dense_reference_test_n"), "astrometric_rms_mas": x.get("astrometric_rms_mas"),
                    "photometric_rms_mag": x.get("photometric_rms_mag"), "runtime_s": x.get("runtime_s")},
            note="Reimplementation/ablation of the stated modelling idea, not a bit-for-bit external pipeline reproduction."))

    hybrid = json.loads((OUT / "hst_hybrid_wpdc_photutils" / "hybrid_summary.json").read_text(encoding="utf-8"))
    for x in hybrid["results"]:
        rows.append(standard_row(
            tier="HST/ACS single-F606W controlled", field=x["cluster"], method_id="astrocfr_photutils_hybrid",
            status="complete_partial_3_of_11", protocol="central 1200x1200 crop; held-out spatial test partition; 2-pixel one-to-one association",
            source="results/hst_hybrid_wpdc_photutils/hybrid_summary.json",
            result={"test_references": x.get("test_references"), "recovery": x.get("test_completeness"),
                    "recovery_metric": "held_out_catalogue_recovery", "high_density_recall": x.get("high_density_v20_recall"),
                    "high_density_n": x.get("high_density_v20_n"), "astrometric_rms_mas": x.get("astrometric_rms_mas"),
                    "photometric_rms_mag": x.get("photometric_rms_mag"), "runtime_s": x.get("runtime_s"),
                    "runtime_s_per_mpix": x.get("runtime_s_per_mpix")},
            note="Only NGC 6397, NGC 6752 and NGC 1851 have an archived hybrid run; it is an ablation, not a claimed combined optimum."))

    present = {(r["field"], r["method_id"]) for r in rows}
    for field in HST_FIELDS:
        for method_id, _, _ in METHODS:
            if (field, method_id) in present:
                continue
            if method_id == "astrocfr_rf":
                note = "Protocol-excluded, not a failed RF result: the released RF is trained on CSST simulated-truth labels with CSST-specific feature normalisation and thresholding. Direct HST transfer is unvalidated; retraining on the ACSGGCT evaluation catalogue would be a separate supervised target-adaptation experiment requiring a pre-registered cross-field or held-out-field design."
                status = "protocol_excluded_no_hst_supervised_adaptation"
            elif method_id == "astrocfr_photutils_hybrid":
                note = "No full 11-field hybrid rerun is archived; only the original three HST fields have a matched-protocol ablation."
                status = "not_run_full_11_field_suite"
            elif method_id in {"dolphot", "crowdsource", "euclid_vvv", "csst_psfnet"}:
                add_incompatible(rows, field, method_id, "HST/ACS single-F606W controlled")
                continue
            else:
                note = "No matched-protocol result is archived."
                status = "not_archived"
            rows.append(standard_row(tier="HST/ACS single-F606W controlled", field=field, method_id=method_id,
                                     status=status, protocol="not run", source="availability audit", note=note))

    sex_recall = (0.916, 0.876, 0.891, 0.837)
    astro_recall = (0.969, 0.931, 0.964, 0.918)
    sex_pos = (32.8, 25.5, 20.2, 23.0)
    astro_pos = (17.2, 8.2, 9.1, 8.8)
    sex_mag = (0.406, 0.497, 0.250, 0.392)
    astro_mag = (0.0596, 0.0575, 0.0802, 0.0684)
    for idx, field in enumerate(CSST_FIELDS):
        for method_id, _, _ in METHODS:
            if method_id == "sep":
                result = {"recovery": sex_recall[idx], "recovery_metric": "supplied_catalogue_recovery", "astrometric_rms_mas": sex_pos[idx], "photometric_rms_mag": sex_mag[idx]}
                rows.append(standard_row(tier="CSST-like full-frame registered simulation", field=field, method_id=method_id,
                                         status="registered_audit_result", protocol="full 9232x9216 frame; supplied-catalogue evaluation", source="tools/build_manuscript_v42_closed_book_scope.py", result=result,
                                         note="Registered calibrated SExtractor audit; not a newly rerun 15-field baseline."))
            elif method_id == "astrocfr_epsf_deblend":
                result = {"recovery": astro_recall[idx], "recovery_metric": "supplied_catalogue_recovery", "astrometric_rms_mas": astro_pos[idx], "photometric_rms_mag": astro_mag[idx]}
                rows.append(standard_row(tier="CSST-like full-frame registered simulation", field=field, method_id=method_id,
                                         status="registered_audit_result", protocol="full 9232x9216 frame; supplied-catalogue evaluation", source="tools/build_manuscript_v42_closed_book_scope.py", result=result,
                                         note="Registered AstroCFR result; do not mix with older central-crop blind-test outputs."))
            elif method_id in {"dolphot", "crowdsource", "euclid_vvv", "csst_psfnet"}:
                add_incompatible(rows, field, method_id, "CSST-like full-frame registered simulation")
            elif method_id == "astrocfr_rf":
                rows.append(standard_row(tier="CSST-like full-frame registered simulation", field=field, method_id=method_id,
                                         status="not_separately_archived", protocol="not run", source="availability audit",
                                         note="No separately aligned RF-only full-frame registered metric is archived."))
            else:
                rows.append(standard_row(tier="CSST-like full-frame registered simulation", field=field, method_id=method_id,
                                         status="not_archived_in_registered_protocol", protocol="not run", source="availability audit",
                                         note="No directly aligned full-frame registered result is archived for this method."))

    fields = list(rows[0])
    with MATRIX.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)

    complete_status = {"complete", "complete_partial_3_of_11", "registered_audit_result"}
    summary = []
    for tier in sorted({r["evidence_tier"] for r in rows}):
        for method_id, label, group in METHODS:
            selected = [r for r in rows if r["evidence_tier"] == tier and r["method_id"] == method_id and r["status"] in complete_status]
            def med(column):
                values = [float(r[column]) for r in selected if r[column] != ""]
                return fmt(statistics.median(values), 4) if values else ""
            summary.append({"evidence_tier": tier, "method_id": method_id, "method": label, "method_group": group,
                            "completed_fields": len(selected), "median_recovery_percent": med("recovery_percent"),
                            "median_high_density_v20_recall_percent": med("high_density_v20_recall_percent"),
                            "median_astrometric_rms_mas": med("astrometric_rms_mas"),
                            "median_photometric_rms_mag": med("photometric_rms_mag"),
                            "median_runtime_s_per_mpix": med("runtime_s_per_mpix")})
    with SUMMARY.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary[0]))
        writer.writeheader(); writer.writerows(summary)

    registry_rows = []
    for method_id, label, group in METHODS:
        method_rows = [r for r in rows if r["method_id"] == method_id]
        registry_rows.append({"method_id": method_id, "method": label, "method_group": group,
                              "complete_or_registered_rows": sum(r["status"] in complete_status for r in method_rows),
                              "unavailable_or_incompatible_rows": sum(r["status"] not in complete_status for r in method_rows),
                              "scope_or_availability": next((r["note"] for r in method_rows if r["status"] not in complete_status), "all matrix rows complete")})
    with REGISTRY.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(registry_rows[0]))
        writer.writeheader(); writer.writerows(registry_rows)

    hst = [r for r in rows if r["evidence_tier"] == "HST/ACS single-F606W controlled" and r["status"] == "complete"]
    spatial = {r["field"]: r for r in hst if r["method_id"] == "astrocfr_spatial_epsf_joint"}
    photutils = {r["field"]: r for r in hst if r["method_id"] == "photutils_psf"}
    common = sorted(set(spatial) & set(photutils))
    recovery_wins = sum(float(spatial[f]["high_density_v20_recall_percent"]) > float(photutils[f]["high_density_v20_recall_percent"]) for f in common)
    pos_wins = sum(float(spatial[f]["astrometric_rms_mas"]) < float(photutils[f]["astrometric_rms_mas"]) for f in common)
    mag_wins = sum(float(spatial[f]["photometric_rms_mag"]) < float(photutils[f]["photometric_rms_mag"]) for f in common)
    hst_summary = [s for s in summary if s["evidence_tier"] == "HST/ACS single-F606W controlled" and s["completed_fields"]]
    csst_summary = [s for s in summary if s["evidence_tier"] == "CSST-like full-frame registered simulation" and s["completed_fields"]]
    pair_rows = []
    for field in common:
        s, p = spatial[field], photutils[field]
        sr, pr = float(s["high_density_v20_recall_percent"]), float(p["high_density_v20_recall_percent"])
        sa, pa = float(s["astrometric_rms_mas"]), float(p["astrometric_rms_mas"])
        sm, pm = float(s["photometric_rms_mag"]), float(p["photometric_rms_mag"])
        pair_rows.append({"field": field, "spatial_high_density_v20_recall_percent": f"{sr:.3f}", "photutils_high_density_v20_recall_percent": f"{pr:.3f}", "delta_recall_percentage_points": f"{sr-pr:.3f}", "spatial_astrometric_rms_mas": f"{sa:.4f}", "photutils_astrometric_rms_mas": f"{pa:.4f}", "delta_astrometric_rms_mas_spatial_minus_photutils": f"{sa-pa:.4f}", "spatial_photometric_rms_mag": f"{sm:.5f}", "photutils_photometric_rms_mag": f"{pm:.5f}", "delta_photometric_rms_mag_spatial_minus_photutils": f"{sm-pm:.5f}"})
    with PAIRWISE.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(pair_rows[0]))
        writer.writeheader(); writer.writerows(pair_rows)
    def markdown_table(items):
        headers = ["method", "completed_fields", "median_recovery_percent", "median_high_density_v20_recall_percent", "median_astrometric_rms_mas", "median_photometric_rms_mag", "median_runtime_s_per_mpix"]
        lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
        for item in items:
            lines.append("| " + " | ".join(str(item[h]) for h in headers) + " |")
        return "\n".join(lines)
    README.write_text(
        "# 15-field all-method evidence matrix\n\n"
        "This is a long-form, machine-readable matrix for 11 ACS Globular Cluster Treasury F606W stacks and four CSST-like full frames. "
        "Blank metric cells mean unavailable/incompatible, not zero performance. The two evidence tiers must not be averaged together.\n\n"
        "## What is directly comparable\n\n"
        "HST rows marked `complete` share a central 1200x1200 single-image crop, spatially held-out catalogue evaluation, and a 2-pixel one-to-one association rule. "
        "CSST rows marked `registered_audit_result` are full-frame supplied-catalogue audits and remain a separate tier. The archived CSST tier contains calibrated SExtractor and the AstroCFR full-frame branch, but no method-identical CSST Photutils/ePSF outputs; blank CSST rows are therefore not zero scores and do not support a CSST ePSF-versus-Photutils claim.\n\n"
        "## HST robust medians across completed fields\n\n" + markdown_table(hst_summary) + "\n\n"
        "## CSST robust medians across registered chips\n\n" + markdown_table(csst_summary) + "\n\n"
        "## Direct AstroCFR spatial-ePSF vs Photutils statement\n\n"
        f"Across the common 11 HST fields, AstroCFR spatial-ePSF joint has higher high-density V<=20 recovery in {recovery_wins}/11 fields, lower reported position RMS in {pos_wins}/11 fields, and lower reported magnitude RMS in {mag_wins}/11 fields. "
        "This is a conditional result for the disclosed single-image protocol; it is not a universal DOLPHOT/ALLFRAME or multi-band-pipeline ranking.\n\n"
        "## Files\n\n"
        f"- `{MATRIX.name}`: every field x every method, including explicit unavailable/incompatible rows.\n"
        f"- `{SUMMARY.name}`: medians within each evidence tier only.\n"
        f"- `{REGISTRY.name}`: method scope and coverage audit.\n"
        f"- `{PAIRWISE.name}`: field-by-field spatial-ePSF versus Photutils differences.\n",
        encoding="utf-8")
    print(f"Wrote {len(rows)} matrix rows to {MATRIX}")


if __name__ == "__main__":
    main()
