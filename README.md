# AstroCFR

AstroCFR — Astronomical Crowded-Field Recovery System — is a deployable system for candidate generation, target-domain adaptation, and crowded-field recovery in large-scale astronomical survey images. The Python package retains the historical `src/wpdc` import path for backward compatibility with released scripts and result manifests.

The repository is organized around reproducible operating points:

- `AstroCFR-RF`: a fast, target-adapted conservative catalogue branch;
- `AstroCFR spatial-ePSF + joint fit`: a slower high-recovery branch for crowded fields;
- classical comparison branches: DAOStarFinder, SEP/SExtractor-style extraction, and Photutils PSFPhotometry.

AstroCFR's deployment protocol is explicitly two-stage: simulation development, followed by lightweight target-domain adaptation using an image-only PSF estimate and a small, spatially disjoint labelled calibration region. The HST adaptation-budget curve reports this calibration cost in target-field tiles and label counts; it does not relabel within-image tiles as independent observations. The density-stratified router is a Pareto policy on registered artificial-star strata, not a universal SOTA claim or a validated automatic image gate. A separate three-field image-only gate audit is archived as a negative control.

The real-field expansion is registered as **4+11**: four original CSST simulated chips plus ten public HST observational fields from PHAT M31, PHATTER M33, the Hubble Tarantula Treasury, and ANGST. All five image-domain branches (DAOStarFinder, SEP, Photutils PSFPhotometry, `astrocfr_epsf`, and the AstroCFR-Photutils hybrid) use the same real crop and held-out catalogue protocol within each admitted field. Fixed-truth morphology simulations remain supplementary stress tests and are never counted among the ten real fields. Large survey and HST files are not committed to Git. Download instructions, provenance and hashes are in `data/README.md`.

## Manuscript package

`supplementary/AstroCFR_Crowded_Field_Manuscript_v44_independent_psf.docx` remains the last fully validated submission manuscript. The v45 scene-scope pair is retained as an archived draft because its three synthetic morphology scenes cannot serve as the requested real-field expansion. The next manuscript revision is gated on the registered 4+10 readiness table in `results/real_field_4plus10/`; incomplete fields are reported as pending rather than converted into simulated substitutes.

The release also contains a common-protocol stratified audit for NGC 6752 and
NGC 1851 (`results/hst_stratified_quality/`). It reports held-out completeness
by magnitude and local density, detection-SNR catalogue-match lower bounds,
conditional astrometric/photometric RMS, and relative wall time. Its spatial-
ePSF ECSV catalogues export reference-free blind-quality metadata and a
documented bitmask. These flags support downstream screening; they are not
blind-purity labels.

## Repository map

```text
src/wpdc/                 reusable AstroCFR/domain-adaptation/ePSF modules (historical path)
experiments/hst/          controlled HST baseline and artificial-star runs
experiments/csst/         CSST challenge-oriented entry points
configs/                  disclosed experiment settings
data/                     download and provenance instructions only
results/                  schema and archival guidance; large outputs stay external
supplementary/            manuscript supplementary material
environment/              version lock and hardware capture template
tools/                    report and figure builders
```

## Quick start

```powershell
python -m pip install -e .
$env:PYTHONPATH = "src/wpdc;experiments/hst"
python experiments/hst/hst_unified_baseline_benchmark.py --skip-artificial --output-dir results/hst_baseline
python experiments/hst/runtime_repeat_ci.py
python experiments/hst/expanded_artificial_ngc6752.py
python experiments/hst/stratified_recovery_quality_flags.py
python experiments/hst/render_stratified_quality_figures.py
python experiments/hst/mast_field_inventory.py --output results/non_globular_field_inventory.json
python experiments/hst/download_non_globular_flc.py --dry-run
python experiments/hst/phat_real_catalogue_benchmark.py --field f15
python experiments/hst/real_field_4plus10_benchmark.py
```

The PHAT and ANGST adapters perform catalogue-conditioned comparisons on real
observations. `real_field_4plus10_benchmark.py` aggregates only completed
real-image runs and writes an explicit acquisition/catalogue/result readiness
gate for all ten registered fields. The old morphology simulation command is
documented in `docs/astrophysical_scene_expansion.md` as a supplementary stress
test only.

Public HST data are not bundled. For the density-gate and parallel-scaling audits, point `--upstream` to the downloaded upstream experiment/data directory:

```powershell
python experiments/hst/automatic_density_gate_diagnostic.py --upstream <path> --cluster ngc6752
python experiments/hst/tile_parallel_scaling.py --upstream <path> --cluster ngc6752 --tile-size 600 --workers 1,2,4
```

The complete n=200-per-stratum artificial-star outputs used in the manuscript are archived with the submission package. Runtime is machine-dependent; record the host and thread settings when reproducing.

For the registered native-FLC multi-exposure backend audit, obtain the MAST
programme 10775 exposure lists described in `data/README.md`, then run:

```powershell
pwsh experiments/hst/run_dolphot_multiepoch.ps1 -Cluster ngc6752
pwsh experiments/hst/run_dolphot_multiepoch.ps1 -Cluster ngc1851
```

Only a run passing its inter-exposure alignment audit may be evaluated against
the same spatial test partition. Do not report a native-FLC artificial-star
result as an identical-scene comparison with the stacked-image experiment.

## Data challenge

The CSST challenge data and rules are distributed by the National Astronomical Data Center at:

<https://nadc.china-vo.org/events/CSSTdatachallenge2026/info/challenge_11th>

Users must follow the challenge licence and redistribution conditions. This repository stores scripts and metadata, not restricted challenge data.

## Naming and scope of claims

AstroCFR claims a bounded crowded-field recovery advantage under the disclosed image, matching, partition and injection protocol. It does not claim universal superiority in astrometric precision, photometric precision, throughput, or catalogue purity. WPDC-2P in the literature is an unrelated weighted polynomial geometric-distortion method; it is not this system.

The current evidence and optional follow-up experiments are summarized in [`docs/submission_gap_audit_v32.md`](docs/submission_gap_audit_v32.md).
The reviewer-facing evidence-boundary record, including the closed-book CSST
pilot and rejected DOLPHOT repeat, is in
[`docs/q3_review_response_audit.md`](docs/q3_review_response_audit.md).
