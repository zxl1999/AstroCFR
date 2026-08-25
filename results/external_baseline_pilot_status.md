# DAOPHOT/Tractor and multi-exposure pilot status

## Scope

The first pilot targets the existing NGC 6752 and NGC 6397 ACS/WFC F606W material. The intended comparison is a common 1200 x 1200 crop, a two-pixel one-to-one association rule, the same quality-selected external catalogue, and train-only spatial registration/zero-point calibration.

## Data and multi-exposure backend

Five native FLC exposures are available for NGC 6397 and five FLC exposures are available for NGC 6752. DOLPHOT 2.1 and its ACS helpers run natively on Windows. The existing NGC 6397 diagnostic produced 25,187 DOLPHOT rows, retained 684 sources in the common crop, and recovered 10.3% of 631 quality references. Its 0.77--1.18 pixel inter-exposure alignment scatter fails the predeclared sub-pixel precision gate, so it is not a publishable multi-exposure ranking.

The existing NGC 6752 retry also fails the same gate: the fitted alignment scatter remains approximately 0.82--0.85 pixels after manual-WCS and cubic retries. It is therefore retained as a registration diagnostic, not as a DOLPHOT performance number.

## Single-image baseline readiness

The ACSGGCT DRZ/F606W images already provide the merged-image input. On NGC 6752, the archived common-crop results are:

| Method | Recovery (%) | Position RMS (mas) | Magnitude RMS (mag) | Runtime (s) |
|---|---:|---:|---:|---:|
| Photutils PSFPhotometry | 76.26 | 1.05 | 0.039 | 14.12 |
| AstroCFR ePSF-deblend | 87.56 | 1.35 | 0.042 | 40.93 |
| AstroCFR spatial-ePSF joint | 88.31 | 1.27 | 0.037 | 55.54 |

These are the valid merged-image reference rows for the external-baseline pilot.

DAOPHOT/ALLSTAR is not available in the current Windows Python environment: PyRAF requires the Unix `fcntl` module and no IRAF/DAOPHOT executable is installed. Tractor is also not yet runnable: the PyPI package resolved to an unrelated alpha package with incompatible Trio dependencies, while the astronomy Tractor implementation requires the astrometry.net stack. No DAOPHOT or Tractor number is reported until a reproducible Linux/WSL or container environment is available.

## Decision

The pilot confirms that the multi-exposure data and DOLPHOT backend are present, but the current FLC registration quality is insufficient for a fair precision leaderboard. The merged single-image AstroCFR/Photutils rows are valid. DAOPHOT and Tractor require a separate reproducible Unix/container setup before they can be added without inventing or substituting results.
