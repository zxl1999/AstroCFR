#!/usr/bin/env python
"""Evaluate a deterministic density-adaptive AstroCFR branch router.

This is a deployment-policy analysis, not a new trained classifier. It uses
the identical fixed-scene NGC 6752 artificial-star aggregates and selects the
measurement branch by the known injection stratum: high-density strata use
the spatial-ePSF/joint branch, while low-density strata use Photutils. The
stratum labels are part of the artificial-star protocol and are not used to
fit model parameters. Proposal recovery and runtime are reported; purity and
measurement RMS are intentionally marked unavailable because this input
summary contains proposal-recovery counts only.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AGGREGATES = ROOT / "results" / "hst_expanded_artificial_ngc6752" / "expanded_artificial_aggregate.csv"
RUNTIME = ROOT / "results" / "hst_controlled_baseline" / "runtime_repeat_ci.json"
OUT = ROOT / "results" / "hst_density_adaptive_routing"


def wilson(k: int, n: int, z: float = 1.959963984540054) -> list[float]:
    if n == 0:
        return [float("nan"), float("nan")]
    p = k / n
    den = 1 + z * z / n
    ctr = (p + z * z / (2 * n)) / den
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return [ctr - half, ctr + half]


def load_rows() -> list[dict]:
    with AGGREGATES.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["mag"] = float(r["mag"])
        r["injected"] = int(r["injected"])
        r["recovered"] = int(r["recovered"])
        r["recovery"] = float(r["recovery"])
    return rows


def load_runtime() -> dict[str, float]:
    payload = json.loads(RUNTIME.read_text(encoding="utf-8"))
    return {r["method"]: float(r["runtime_median"]) for r in payload["methods"]}


def select(policy: str, density: str) -> str:
    if policy == "photutils_only":
        return "Photutils PSFPhotometry"
    if policy == "epsf_only":
        return "WPDC ePSF + residual deblend"
    if policy == "spatial_epsf_only":
        return "WPDC spatial ePSF + joint fit"
    if policy == "density_adaptive":
        return "WPDC spatial ePSF + joint fit" if density == "high" else "Photutils PSFPhotometry"
    raise ValueError(policy)


def evaluate(rows: list[dict], runtime: dict[str, float], policy: str) -> dict:
    selected = []
    recovered = injected = 0
    weighted_runtime = 0.0
    for density in ("high", "low"):
        for mag in (20.0, 22.0):
            method = select(policy, density)
            matches = [r for r in rows if r["density_band"] == density and r["mag"] == mag and r["method"] == method]
            if len(matches) != 1:
                raise RuntimeError(f"Expected one aggregate row for {density}/{mag}/{method}")
            r = matches[0]
            injected += r["injected"]
            recovered += r["recovered"]
            weighted_runtime += r["injected"] * runtime[method]
            selected.append({"mag": int(mag), "density_band": density, "method": method,
                             "injected": r["injected"], "recovered": r["recovered"],
                             "recovery": r["recovery"], "recovery_ci95": wilson(r["recovered"], r["injected"])})
    return {
        "policy": policy,
        "selected_strata": selected,
        "injected": injected,
        "recovered": recovered,
        "recovery": recovered / injected,
        "recovery_ci95": wilson(recovered, injected),
        "runtime_s_per_mpix_weighted": weighted_runtime / injected,
        "purity_lower_bound": None,
        "position_rms_mas": None,
        "magnitude_rms_mag": None,
        "metric_note": "Artificial-star proposal recovery summary has no per-candidate purity or measurement residuals; these fields are not inferred.",
    }


def main() -> None:
    rows = load_rows()
    runtime = load_runtime()
    OUT.mkdir(parents=True, exist_ok=True)
    payload = {
        "protocol": {
            "cluster": "ngc6752",
            "input": str(AGGREGATES.relative_to(ROOT)),
            "association_radius_px": 2,
            "confidence_interval": "Wilson 95%",
            "router": "high-density -> spatial-ePSF+joint; low-density -> Photutils PSFPhotometry",
            "same_fixed_scenes": True,
            "scope": "deployment-policy analysis; no refit or threshold selection",
        },
        "policies": [evaluate(rows, runtime, p) for p in ("photutils_only", "epsf_only", "spatial_epsf_only", "density_adaptive")],
    }
    (OUT / "density_adaptive_branch_routing.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
