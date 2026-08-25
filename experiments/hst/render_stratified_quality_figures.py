#!/usr/bin/env python
"""Render publication figures from the machine-readable stratification audit.

This deliberately does not rerun source extraction: it visualises the exact
CSV written by ``stratified_recovery_quality_flags.py``.  Completeness and
catalogue-match intervals are Wilson 95% intervals; RMS values are conditional
on matched held-out references and must not be read as blind-catalogue errors.
"""
from __future__ import annotations

import ast
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
INPUT = ROOT / "results" / "hst_stratified_quality" / "stratified_recovery_precision.csv"
OUT = INPUT.parent


def rows():
    with INPUT.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def value(row, name):
    raw = row.get(name, "")
    return np.nan if raw in ("", "None", None) else float(raw)


def interval(row, name):
    raw = row.get(name, "")
    return None if raw in ("", "None", None) else ast.literal_eval(raw)


def plot_metric(ax, subset, value_key, ci_key=None, ylabel="", ylim=None):
    labels = [r["bin"] for r in subset]
    xs = np.arange(len(subset))
    y = np.array([value(r, value_key) for r in subset])
    if ci_key:
        limits = [interval(r, ci_key) for r in subset]
        err = np.array([[np.nan if lim is None else y[i] - lim[0] for i, lim in enumerate(limits)],
                        [np.nan if lim is None else lim[1] - y[i] for i, lim in enumerate(limits)]])
        ax.errorbar(xs, y, yerr=err, marker="o", lw=1.2, capsize=2)
    else:
        ax.plot(xs, y, marker="o", lw=1.2)
    ax.set_xticks(xs, labels, rotation=22, ha="right")
    ax.set_ylabel(ylabel)
    if ylim:
        ax.set_ylim(*ylim)
    ax.grid(alpha=.22)


def main():
    records = rows()
    methods = sorted({row["method"] for row in records})
    labels = {row["method"]: row["label"] for row in records}
    colors = dict(zip(methods, plt.cm.tab10(np.linspace(0, 1, len(methods)))) )
    for cluster in ("ngc6752", "ngc1851"):
        fig, axes = plt.subplots(3, 2, figsize=(12.8, 12.0), constrained_layout=True)
        for method in methods:
            data = [r for r in records if r["cluster"] == cluster and r["method"] == method]
            mag = [r for r in data if r["axis"] == "magnitude"]
            density = [r for r in data if r["axis"] == "density"]
            snr = [r for r in data if r["axis"] == "detection_snr"]
            color = colors[method]
            for ax, subset, key, ci, ylabel in (
                (axes[0, 0], mag, "completeness", "completeness_ci95", "Completeness"),
                (axes[0, 1], density, "completeness", "completeness_ci95", "Completeness"),
                (axes[1, 0], snr, "catalogue_match_lower_bound", "catalogue_match_ci95", "Catalogue-match lower bound"),
                (axes[1, 1], mag, "position_rms_mas", None, "Conditional position RMS / mas"),
                (axes[2, 0], mag, "magnitude_rms_mag", None, "Conditional magnitude RMS / mag"),
            ):
                xs = np.arange(len(subset)); y = np.array([value(r, key) for r in subset])
                if ci:
                    limits = [interval(r, ci) for r in subset]
                    valid = [q is not None and q[0] is not None and q[1] is not None for q in limits]
                    err = np.array([[np.nan if not valid[i] else y[i] - q[0] for i, q in enumerate(limits)],
                                    [np.nan if not valid[i] else q[1] - y[i] for i, q in enumerate(limits)]])
                    ax.errorbar(xs, y, yerr=err, marker="o", color=color, lw=1.1, capsize=2, label=labels[method])
                else:
                    ax.plot(xs, y, marker="o", color=color, lw=1.1, label=labels[method])
                ax.set_xticks(xs, [r["bin"] for r in subset], rotation=20, ha="right")
                ax.set_ylabel(ylabel); ax.grid(alpha=.22)
            runtime = value(mag[0], "runtime_s_per_mpix")
            axes[2, 1].bar(labels[method], runtime, color=color)
        axes[0, 0].set_title("Held-out completeness by reference magnitude"); axes[0, 0].set_ylim(0, 1.05)
        axes[0, 1].set_title("Held-out completeness by local density"); axes[0, 1].set_ylim(0, 1.05)
        axes[1, 0].set_title("Detection-SNR catalogue-match lower bound"); axes[1, 0].set_ylim(0, 1.05)
        axes[1, 1].set_title("Matched-reference astrometric error by magnitude")
        axes[2, 0].set_title("Matched-reference photometric error by magnitude")
        axes[2, 1].set(title="Single-run relative wall time", ylabel="s / MPix")
        axes[2, 1].tick_params(axis="x", rotation=45, labelsize=7)
        axes[0, 0].legend(fontsize=7, ncol=2, loc="lower left")
        fig.suptitle(f"{cluster.upper()}: common-protocol stratified audit", fontsize=15)
        fig.savefig(OUT / f"{cluster}_stratified_recovery_precision.png", dpi=240)
        plt.close(fig)


if __name__ == "__main__":
    main()
