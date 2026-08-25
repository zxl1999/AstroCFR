#!/usr/bin/env python
"""Paired audit of empirical versus independent Anderson PSF injections."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import binomtest


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "results/non_globular_runs/m31_b21_f15/matched_coordinate_scene"
SINGLE = BASE / "strict_single_injection"
OUT = BASE / "independent_psf_validation"
METHODS = ("dao", "sep", "photutils", "astrocfr_epsf")
PSFS = ("empirical", "anderson")
STRATA = (("low", 24.5), ("low", 26.5), ("high", 24.5), ("high", 26.5))


def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    p = k / n
    denominator = 1 + z * z / n
    middle = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [max(0.0, middle - half), min(1.0, middle + half)]


def paired_test(a: list[bool], b: list[bool]) -> dict[str, float | int]:
    a_only = sum(x and not y for x, y in zip(a, b))
    b_only = sum(not x and y for x, y in zip(a, b))
    discordant = a_only + b_only
    return {
        "a_only": a_only,
        "b_only": b_only,
        "discordant": discordant,
        "mcnemar_exact_p": float(binomtest(min(a_only, b_only), discordant, 0.5).pvalue) if discordant else 1.0,
    }


def bootstrap_paired_difference(a: list[bool], b: list[bool], seed: int, replicates: int = 20000) -> list[float]:
    aa, bb = np.asarray(a, float), np.asarray(b, float)
    rng = np.random.default_rng(seed)
    differences = np.empty(replicates, float)
    for start in range(0, replicates, 1000):
        count = min(1000, replicates - start)
        indices = rng.integers(0, len(aa), size=(count, len(aa)))
        differences[start:start + count] = (aa[indices] - bb[indices]).mean(axis=1)
    return [float(x) for x in np.quantile(differences, [0.025, 0.975])]


def read_rows(method: str, psf: str) -> dict[str, dict]:
    suffix = method + ("_anderson" if psf == "anderson" else "")
    payload = json.loads((SINGLE / f"{suffix}_summary.json").read_text(encoding="utf-8"))
    return {str(r["fake_id"]): r for r in payload["rows"] if r.get("status") == "eligible"}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    by = {psf: {method: read_rows(method, psf) for method in METHODS} for psf in PSFS}
    common_by_psf = {
        psf: sorted(set.intersection(*(set(by[psf][method]) for method in METHODS)), key=int)
        for psf in PSFS
    }
    all_common = sorted(set.intersection(*(set(ids) for ids in common_by_psf.values())), key=int)

    recovery = []
    comparisons = []
    seed = 20260812
    for psf in PSFS:
        ids = common_by_psf[psf]
        for density, magnitude in STRATA:
            use = [i for i in ids if by[psf]["photutils"][i]["density_band"] == density and float(by[psf]["photutils"][i]["input_vegamag_f475w"]) == magnitude]
            for method in METHODS:
                rows = [by[psf][method][i] for i in use]
                recovered = [bool(r["recovered"]) for r in rows]
                distances = [float(r["injection_nearest_px"]) for r in rows if r["recovered"]]
                k, n = sum(recovered), len(recovered)
                ci = wilson(k, n)
                recovery.append({
                    "injection_psf": psf,
                    "density_band": density,
                    "input_vegamag_f475w": magnitude,
                    "method": method,
                    "common_eligible": n,
                    "recovered": k,
                    "recovery": k / n,
                    "ci95_low_wilson": ci[0],
                    "ci95_high_wilson": ci[1],
                    "nearest_detection_radial_rms_px": float(np.sqrt(np.mean(np.square(distances)))) if distances else None,
                    "runtime_per_trial_median_s": float(np.median([r["runtime_s"] for r in rows])),
                })
            astro = [bool(by[psf]["astrocfr_epsf"][i]["recovered"]) for i in use]
            for method in ("dao", "sep", "photutils"):
                other = [bool(by[psf][method][i]["recovered"]) for i in use]
                ci = bootstrap_paired_difference(astro, other, seed)
                seed += 1
                comparisons.append({
                    "comparison": "between_methods",
                    "injection_psf": psf,
                    "density_band": density,
                    "input_vegamag_f475w": magnitude,
                    "method_a": "astrocfr_epsf",
                    "method_b": method,
                    "n": len(use),
                    "recovery_difference_a_minus_b": float(np.mean(astro) - np.mean(other)),
                    "paired_bootstrap_ci95_low": ci[0],
                    "paired_bootstrap_ci95_high": ci[1],
                    **paired_test(astro, other),
                })

    # Injection-model robustness on exactly the same coordinate set.  This is
    # deliberately method-wise: it asks how much a method's measured recovery
    # changes when only the artificial-star PSF generator is replaced.
    injection_model_comparisons = []
    for density, magnitude in STRATA:
        use = [i for i in all_common if by["anderson"]["photutils"][i]["density_band"] == density and float(by["anderson"]["photutils"][i]["input_vegamag_f475w"]) == magnitude]
        for method in METHODS:
            independent = [bool(by["anderson"][method][i]["recovered"]) for i in use]
            empirical = [bool(by["empirical"][method][i]["recovered"]) for i in use]
            ci = bootstrap_paired_difference(independent, empirical, seed)
            seed += 1
            injection_model_comparisons.append({
                "comparison": "anderson_minus_empirical_injection",
                "density_band": density,
                "input_vegamag_f475w": magnitude,
                "method": method,
                "n": len(use),
                "recovery_anderson": float(np.mean(independent)),
                "recovery_empirical": float(np.mean(empirical)),
                "recovery_difference": float(np.mean(independent) - np.mean(empirical)),
                "paired_bootstrap_ci95_low": ci[0],
                "paired_bootstrap_ci95_high": ci[1],
                **paired_test(independent, empirical),
            })

    payload = {
        "scope": "Strict one-star-at-a-time recovery in one PHAT F475W DRZ image. The official Anderson F475W PSF is independent of AstroCFR's image-derived recovery ePSF.",
        "protocol": {
            "balanced_input": "25 coordinates per ACS logical extension x density x magnitude stratum (200 attempted)",
            "denominator": "within each injection model, intersection of positions baseline-clear for all four methods",
            "recovery_rule": "a new detection within 2 DRZ pixels after injection, with no baseline detection within 2 pixels",
            "intervals": "Wilson 95% intervals for individual recovery; deterministic 20,000-replicate paired bootstrap intervals for recovery differences; exact two-sided McNemar p values",
            "independent_psf": "official Anderson ACS/WFC F475W 9 x 10 spatial-grid ePSF, projected through the WCS of three registered FLC exposures, convolved with a fixed 0.55-pixel Gaussian output kernel to match the measured 2.2-pixel DRZ core scale, and normalized on the DRZ grid",
            "limitation": "the independent renderer approximates the local dithered DRZ PSF but does not reproduce the full AstroDrizzle kernel or correlated noise; this supports candidate-recovery claims, not final photometric superiority",
        },
        "common_n_by_injection_psf": {k: len(v) for k, v in common_by_psf.items()},
        "common_n_across_both_injection_psfs": len(all_common),
        "common_ids_by_injection_psf": common_by_psf,
        "recovery": recovery,
        "between_method_paired_tests": comparisons,
        "injection_model_paired_tests": injection_model_comparisons,
    }
    (OUT / "independent_psf_validation.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    for filename, rows in (
        ("independent_psf_recovery.csv", recovery),
        ("independent_psf_method_tests.csv", comparisons),
        ("injection_model_robustness.csv", injection_model_comparisons),
    ):
        with (OUT / filename).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
            writer.writeheader(); writer.writerows(rows)

    labels = {"dao": "DAO", "sep": "SEP", "photutils": "Photutils", "astrocfr_epsf": "AstroCFR ePSF"}
    colors = {"dao": "#4C78A8", "sep": "#9C755F", "photutils": "#59A14F", "astrocfr_epsf": "#E15759"}
    fig, axes = plt.subplots(1, 2, figsize=(10.2, 4.0), constrained_layout=True)
    x = np.arange(len(STRATA)); width = 0.19
    independent_rows = [r for r in recovery if r["injection_psf"] == "anderson"]
    for j, method in enumerate(METHODS):
        selected = [next(r for r in independent_rows if r["method"] == method and r["density_band"] == d and r["input_vegamag_f475w"] == m) for d, m in STRATA]
        y = np.array([r["recovery"] for r in selected])
        lo = np.maximum(0.0, y - np.array([r["ci95_low_wilson"] for r in selected]))
        hi = np.maximum(0.0, np.array([r["ci95_high_wilson"] for r in selected]) - y)
        axes[0].bar(x + (j - 1.5) * width, y, width, color=colors[method], label=labels[method], yerr=np.vstack([lo, hi]), capsize=2)
    axes[0].set_xticks(x, [f"{d}\nF475W={m:.1f}" for d, m in STRATA])
    axes[0].set_ylim(0, 1.08); axes[0].set_ylabel("Strict recovery fraction")
    axes[0].grid(axis="y", alpha=0.22); axes[0].legend(frameon=False, fontsize=8, ncol=2, loc="lower left")
    faint = [r for r in injection_model_comparisons if r["input_vegamag_f475w"] == 26.5 and r["method"] == "astrocfr_epsf"]
    xpos = np.arange(2); values = [r["recovery_difference"] for r in faint]
    lows = [r["recovery_difference"] - r["paired_bootstrap_ci95_low"] for r in faint]
    highs = [r["paired_bootstrap_ci95_high"] - r["recovery_difference"] for r in faint]
    axes[1].bar(xpos, values, color="#E15759", yerr=np.vstack([lows, highs]), capsize=4)
    axes[1].axhline(0, color="black", lw=0.8)
    axes[1].set_xticks(xpos, [r["density_band"] for r in faint])
    axes[1].set_ylabel("AstroCFR recovery change\n(Anderson - empirical injection)")
    axes[1].grid(axis="y", alpha=0.22)
    axes[0].set_title("(a) Independent Anderson-PSF recovery")
    axes[1].set_title("(b) Injection-model robustness, F475W=26.5")
    fig.savefig(OUT / "independent_anderson_psf_validation.png", dpi=300, bbox_inches="tight")
    plt.close(fig)

    lines = [
        "# Independent Anderson-PSF injection validation",
        "",
        payload["scope"],
        "",
        f"Common denominator: {len(common_by_psf['anderson'])} coordinates for the Anderson experiment; {len(all_common)} coordinates are common to both injection models.",
        "",
        "## Independent Anderson injection",
        "",
        "|Density|F475W|Method|n|Recovered|Recovery [95% Wilson CI]|Nearest-detection RMS (px)|Median trial time (s)|",
        "|---|---:|---|---:|---:|---|---:|---:|",
    ]
    for r in independent_rows:
        lines.append(f"|{r['density_band']}|{r['input_vegamag_f475w']:.1f}|{labels[r['method']]}|{r['common_eligible']}|{r['recovered']}|{r['recovery']:.3f} [{r['ci95_low_wilson']:.3f}, {r['ci95_high_wilson']:.3f}]|{r['nearest_detection_radial_rms_px']:.3f}|{r['runtime_per_trial_median_s']:.3f}|")
    lines += ["", "## AstroCFR versus baselines under independent injection", "", "|Density|F475W|Baseline|n|Recovery difference [paired bootstrap 95% CI]|AstroCFR-only|Baseline-only|Exact McNemar p|", "|---|---:|---|---:|---|---:|---:|---:|"]
    for r in comparisons:
        if r["injection_psf"] != "anderson":
            continue
        lines.append(f"|{r['density_band']}|{r['input_vegamag_f475w']:.1f}|{labels[r['method_b']]}|{r['n']}|{r['recovery_difference_a_minus_b']:+.3f} [{r['paired_bootstrap_ci95_low']:+.3f}, {r['paired_bootstrap_ci95_high']:+.3f}]|{r['a_only']}|{r['b_only']}|{r['mcnemar_exact_p']:.4g}|")
    lines += [
        "",
        "## Interpretation boundary",
        "",
        "The independent injection removes exact reuse of AstroCFR's image-derived ePSF as the artificial-star generator. The renderer uses an official Anderson F475W library PSF, three accepted FLC WCS solutions, and a fixed 0.55-pixel output-grid broadening calibrated before the final run to match the measured 2.2-pixel DRZ core. It is not a full AstroDrizzle re-simulation. Therefore these data strengthen a bounded single-stack candidate-recovery claim; they do not establish blind catalogue purity, a DOLPHOT-equivalent multi-exposure result, or superior final astrometry/photometry.",
    ]
    (OUT / "INDEPENDENT_PSF_VALIDATION.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "anderson_common_n": len(common_by_psf["anderson"]),
        "both_psfs_common_n": len(all_common),
        "anderson_faint_recovery": [r for r in independent_rows if r["input_vegamag_f475w"] == 26.5],
        "anderson_faint_tests": [r for r in comparisons if r["injection_psf"] == "anderson" and r["input_vegamag_f475w"] == 26.5],
        "astrocfr_faint_injection_robustness": faint,
    }, indent=2))


if __name__ == "__main__":
    main()
