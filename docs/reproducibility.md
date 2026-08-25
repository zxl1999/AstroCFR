# Reproducibility checklist

1. Download permitted data using the URLs in `data/README.md`.
2. Verify byte counts and SHA-256 hashes.
3. Install `environment/requirements-lock.txt`.
4. Run the HST benchmark with the YAML settings in `configs/hst_unified_baseline.yaml`.
5. Run the runtime repeat script and the expanded artificial-star script.
6. Archive stdout, JSON/CSV results, figures and the hardware capture.

All reported recovery intervals are binomial Wilson intervals. Position and magnitude RMS intervals are conditional residual-bootstrap intervals. Timing intervals are based on five repeats after one warm-up run.
