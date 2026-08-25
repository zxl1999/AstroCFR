# Windows DOLPHOT joint-run diagnostic: NGC 6397

This is an external-backend reproducibility diagnostic, not an approved WPDC manuscript result.

## Protocol

Five native ACS/WFC F606W FLC exposures were copied into an ignored work directory, preprocessed with `acsmask` and `calcsky`, and processed with DOLPHOT 2.1 ACS. Coordinates were transformed from the reference FLC through its CPDIS-aware WCS into the ACSGGCT mosaic. A residual six-parameter affine transform and a scalar F606W zero point used only spatial partitions 0/1; partition 2 was untouched for all reported metrics.

- Test quality references: 631
- Retained DOLPHOT sources in central crop: 684
- Test recovery at 2 px: 0.1030
- Test 1D astrometric RMS: 4.43 mas
- Test photometric RMS after train-only scalar zero point: 0.0899 mag
- Alignment matches / retained inliers: 56 / 55

## Interpretation constraint

DOLPHOT joint.warnings reports 0.77--1.18 pixel inter-exposure alignment scatter for three 15-s frames; do not interpret this pilot as a high-precision multi-epoch baseline or include it in manuscript results.
The output must remain outside the main comparison table unless re-registration reduces this warning and an independently repeated test confirms the result.