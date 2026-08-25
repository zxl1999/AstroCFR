# AstroCFR submission release

Prepared version: `v1.6.2` (GitHub upload package; create this tag after pushing)

The earlier local `v1.0.0` tag remains the WPDC-named v30 snapshot, `v1.1.0` is the first AstroCFR-named manuscript snapshot, and `v1.1.1` updates the renamed repository URL. Version `v1.2.0` is the focused v33 manuscript release: its main text contains five Results themes and its detailed classifier evidence is in a standalone supplement.

Version `v1.6.2` retains the v41 manuscript pair and adds WCS-derived FLC dither initialisation plus a cubic alignment diagnostic. It confirms that the previous zero-match DOLPHOT failure was caused by 80--255 pixel dithers outside the original search range, but the best recovered alignment scatter remains 0.817 pixels and is rejected. These diagnostics do not add a manuscript efficacy claim. After upload, commit the repository contents, create the annotated `v1.6.2` tag, and publish `main` and `v1.6.2` to the AstroCFR repository URL before using a release archive in a manuscript submission.

- Tag to create after upload: `v1.6.2`
- Repository: <https://github.com/zxl1999/AstroCFR>
- Source archive after push: <https://github.com/zxl1999/AstroCFR/archive/refs/tags/v1.6.2.zip>
- Archival DOI: none assigned; a Zenodo DOI may be added only after an archive exists

## Reproducibility contents

- reusable implementation: `src/wpdc/` (historical package path; manuscript name AstroCFR)
- controlled HST experiments: `experiments/hst/`
- CSST/simulation adapters and ablations: `experiments/csst/`, `experiments/simulation/`
- machine-readable result summaries: `results/`
- reference-free ECSV quality catalogues and their bitmask schema: `results/hst_stratified_quality/`
- provenance, byte counts, and SHA-256 hashes: `data/manifest.csv`
- main CPU dependency lock: `environment/requirements-lock.txt`
- observed CNN CPU/GPU benchmark environment: `supplementary/environment_cpu_gpu.yml` and `supplementary/requirements-cpu-gpu-tested.txt`
- manuscript builders and reporting utilities: `tools/`

Large FITS products, challenge data, locally compiled DOLPHOT files, caches, and transient logs are excluded. Public inputs remain at their original NADC or MAST locations and are identified by the manifest. Negative DOLPHOT and differentiable-photometry pilots are diagnostic records, not manuscript efficacy results.
