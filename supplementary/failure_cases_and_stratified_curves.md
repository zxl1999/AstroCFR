# Failure-case and stratified-recovery diagnostics

The diagnostic figures use the same public HST/ACS images, quality-filtered reference catalogues, central 1200 x 1200 crop, 2-pixel association rule, and image-only WPDC candidate files used by the controlled evaluation.

- `results/hst_failure_cases/fig_failure_cases.png`: high-crowding miss in NGC 6752, bright-star artifact region in NGC 6752, and a difficult NGC 1851 held-out domain-adaptation region.
- `results/hst_failure_cases/fig_density_magnitude_recovery.png`: fixed artificial-star recovery curves split by low density (0–1 neighbours within 10 pixels) and high density (at least 3 neighbours), with V=20/22 magnitude strata and Wilson 95% intervals.
- `results/hst_failure_cases/failure_case_metrics.json`: coordinates and selection statistics for every displayed failure case.

The figures are diagnostic rather than additional tuned performance claims; the displayed locations are selected from the held-out catalogue after the candidate run.
