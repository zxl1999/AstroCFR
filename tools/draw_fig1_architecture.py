#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Draw the WPDC architecture figure with separated module annotations."""
from __future__ import annotations

from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "figures" / "fig1_wpdc_architecture_redrawn.png"


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    titles = [
        "Raw CSST-like\nchip image", "Adaptive\nbackground model",
        "Multi-branch\nsource detection", "Bright-star and\nblend recovery",
        "Point-source\nclassification", "Astrometric\nrefinement",
        "Photometric\ncalibration", "Final\ncatalogue",
    ]
    details = [
        "flat/dark/readout\nnoise-aware pixels", "2D background + RMS\nrobust interpolation",
        "DAO proposals +\nbranch thresholds", "mask, L1/L2,\nresidual deblending",
        "morphology +\nRF/CNN evidence", "WCS + polynomial/LUT\ncorrection",
        "aperture/ePSF flux +\nzero-point refinement", "positions, magnitudes,\nquality flags",
    ]
    colors = ["#eaf2f8"] * 8
    fig, ax = plt.subplots(figsize=(12.4, 6.4), dpi=220)
    ax.set_xlim(0, 12.4); ax.set_ylim(0, 6.4); ax.axis("off")
    ax.text(6.2, 6.07, "WPDC end-to-end multimedia image-processing architecture",
            ha="center", va="center", fontsize=18, fontweight="bold")
    ax.text(.46, 5.48, "Candidate construction", ha="left", va="center", fontsize=10.5, color="#4d5966", fontweight="bold")
    ax.text(.46, 2.76, "Candidate screening, measurement, and catalogue assembly", ha="left", va="center", fontsize=10.5, color="#4d5966", fontweight="bold")
    width, height = 2.28, 1.42
    positions = [(0.45, 3.77), (3.37, 3.77), (6.29, 3.77), (9.21, 3.77),
                 (9.21, 1.03), (6.29, 1.03), (3.37, 1.03), (0.45, 1.03)]
    for i, (title, detail) in enumerate(zip(titles, details)):
        x, y = positions[i]
        box = FancyBboxPatch((x, y), width, height, boxstyle="round,pad=0.025,rounding_size=0.03",
                             linewidth=1.5, edgecolor="#1f5f8b", facecolor=colors[i])
        ax.add_patch(box)
        ax.text(x + width/2, y + .94, title, ha="center", va="center", fontsize=11.6,
                fontweight="bold", linespacing=1.05)
        ax.plot([x+.16, x+width-.16], [y+.60, y+.60], color="#9eb7ca", lw=.8)
        ax.text(x + width/2, y + .31, detail, ha="center", va="center", fontsize=8.8,
                color="#4d5966", linespacing=1.12)
    for i in range(3):
        x, y = positions[i]
        ax.annotate("", xy=(positions[i+1][0]-.10, y+height/2), xytext=(x+width+.10, y+height/2),
                    arrowprops={"arrowstyle": "-|>", "lw": 1.25, "color": "#2f3e4d"})
    ax.annotate("", xy=(positions[4][0]+width/2, positions[4][1]+height+.08), xytext=(positions[3][0]+width/2, positions[3][1]-.08),
                arrowprops={"arrowstyle": "-|>", "lw": 1.25, "color": "#2f3e4d"})
    for i in range(4, 7):
        x, y = positions[i]
        ax.annotate("", xy=(positions[i+1][0]+width+.10, y+height/2), xytext=(x-.10, y+height/2),
                    arrowprops={"arrowstyle": "-|>", "lw": 1.25, "color": "#2f3e4d"})
    ax.text(6.2, .35, "Simulation development → lightweight target adaptation → science-ready survey catalogue",
            ha="center", va="center", fontsize=10.8, color="#263746")
    fig.savefig(OUT, dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(OUT)


if __name__ == "__main__":
    main()
