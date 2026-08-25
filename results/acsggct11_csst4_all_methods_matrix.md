# 15-field all-method evidence matrix

This is a long-form, machine-readable matrix for 11 ACS Globular Cluster Treasury F606W stacks and four CSST-like full frames. Blank metric cells mean unavailable/incompatible, not zero performance. The two evidence tiers must not be averaged together.

## What is directly comparable

HST rows marked `complete` share a central 1200x1200 single-image crop, spatially held-out catalogue evaluation, and a 2-pixel one-to-one association rule. CSST rows marked `registered_audit_result` are full-frame supplied-catalogue audits and remain a separate tier. The archived CSST tier contains calibrated SExtractor and the AstroCFR full-frame branch, but no method-identical CSST Photutils/ePSF outputs; blank CSST rows are therefore not zero scores and do not support a CSST ePSF-versus-Photutils claim.

## HST robust medians across completed fields

| method | completed_fields | median_recovery_percent | median_high_density_v20_recall_percent | median_astrometric_rms_mas | median_photometric_rms_mag | median_runtime_s_per_mpix |
|---|---|---|---|---|---|---|
| DAOStarFinder | 11 | 22.1380 | 45.8590 | 2.4337 | 0.2000 | 0.1090 |
| SEP/SExtractor-style | 11 | 44.3550 | 51.8270 | 6.3103 | 0.2926 | 6.3730 |
| Photutils PSFPhotometry | 11 | 22.1750 | 45.8590 | 3.8964 | 0.1790 | 7.7760 |
| AstroCFR ePSF + residual deblend | 11 | 35.6840 | 66.6670 | 4.2506 | 0.0833 | 24.8500 |
| AstroCFR spatial-ePSF joint | 11 | 52.0250 | 86.2630 | 2.4417 | 0.0875 | 45.6500 |
| AstroCFR+Photutils hybrid | 3 | 88.2150 | 66.6670 | 1.3753 | 0.0560 | 13.5420 |
| Global empirical ePSF + neighbour joint | 11 | 82.7010 |  | 3.8216 | 0.1236 |  |
| Three-Gaussian dPSF + neighbour joint | 11 | 82.6250 |  | 3.7778 | 0.1237 |  |
| Spatial empirical ePSF + neighbour joint | 11 | 83.0050 |  | 2.8974 | 0.1180 |  |

## CSST robust medians across registered chips

| method | completed_fields | median_recovery_percent | median_high_density_v20_recall_percent | median_astrometric_rms_mas | median_photometric_rms_mag | median_runtime_s_per_mpix |
|---|---|---|---|---|---|---|
| SEP/SExtractor-style | 4 | 88.3500 |  | 24.2500 | 0.3990 |  |
| AstroCFR ePSF + residual deblend | 4 | 94.7500 |  | 8.9500 | 0.0640 |  |

## Direct AstroCFR spatial-ePSF vs Photutils statement

Across the common 11 HST fields, AstroCFR spatial-ePSF joint has higher high-density V<=20 recovery in 11/11 fields, lower reported position RMS in 7/11 fields, and lower reported magnitude RMS in 8/11 fields. This is a conditional result for the disclosed single-image protocol; it is not a universal DOLPHOT/ALLFRAME or multi-band-pipeline ranking.

## Files

- `acsggct11_csst4_all_methods_matrix.csv`: every field x every method, including explicit unavailable/incompatible rows.
- `acsggct11_csst4_method_summary.csv`: medians within each evidence tier only.
- `acsggct11_csst4_method_registry.csv`: method scope and coverage audit.
- `acsggct11_spatial_vs_photutils_by_field.csv`: field-by-field spatial-ePSF versus Photutils differences.
