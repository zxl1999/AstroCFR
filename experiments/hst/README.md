# Controlled HST experiments

`hst_unified_baseline_benchmark.py` evaluates DAOStarFinder, SEP, Photutils PSFPhotometry, AstroCFR-RF, AstroCFR ePSF+deblend and AstroCFR spatial-ePSF+joint fitting under the same ACSGGCT image crop, matching radius, spatial test partition and artificial-star protocol. Internal `wpdc_*` method keys remain unchanged for result-schema compatibility.

`target_adaptation_budget_curve.py` measures the two-stage deployment budget: CSST-like simulation development followed by target-image PSF estimation and a small labelled calibration-tile adaptation on three HST/ACS fields. It reports a spatially held-out learning curve rather than treating tiles as independent telescope images.

`hybrid_wpdc_photutils_benchmark.py` evaluates the negative ablation in which AstroCFR ePSF/residual candidates initialize Photutils PSFPhotometry. It is archived to document that candidate-level fusion with a fixed Gaussian PSF does not automatically improve the recovery--precision trade-off.

`joint_group_deblend_pilot.py` is an exploratory group-wise spatial-ePSF branch. It jointly fits connected candidate groups with a shared local background and non-negative source fluxes. The first NGC 6752 run is archived separately and is not a manuscript claim until replicated.

`evaluate_dolphot_joint_ngc6397.py` evaluates an ignored, Windows-native
DOLPHOT 2.1 five-FLC NGC 6397 joint-run diagnostic with the same ACSGGCT crop,
2-pixel rule, and spatially held-out reference protocol. It is intentionally
not a manuscript-result generator: the available short-exposure pilot has
0.77--1.18-pixel inter-exposure alignment scatter. Details and the decision
record are in `results/hst_multiepoch_backend_audit.md`.

`differentiable_group_photometry.py` is the PyTorch CUDA diagnostic for local
pixel-level blend-group forward modelling. Its first NGC 6752 result is a
negative ablation: the unbatched GPU refiner is slower and less accurate than
the existing CPU spatial-ePSF group fitter. It is excluded from the manuscript;
see `results/hst_differentiable_group/README.md`.

`automatic_density_gate_diagnostic.py` tests whether the registered
artificial-star density policy can be inferred without catalogue access at
deployment. It calibrates only in the left spatial strip and audits the right
strip on all three HST fields. The inconsistent held-out artificial-position
sensitivity is retained as a negative control; it prevents the pre-defined
density router from being overstated as an automatic image gate.

`tile_parallel_scaling.py` measures 1/2/4-process scaling of the ePSF plus
residual-deblend branch on identical HST tiles. It records wall time,
throughput, aggregate parent-plus-worker RSS, and candidate-count invariance.
The claim is limited to tile-level parallelism on one workstation.

`mast_field_inventory.py` queries MAST metadata for the non-globular expansion
shortlist. It downloads no FITS data and does not certify a field; use the
admission gates in `docs/non_globular_expansion_plan.md` before running any
multi-exposure comparison.

`m31_strict_single_injection.py` performs one-star-at-a-time recovery on the
PHAT B21-F15 F475W DRZ image. With `--injection-psf anderson`, artificial stars
are rendered from the official Anderson ACS/WFC F475W library independently
of AstroCFR's image-derived recovery ePSF. `audit_anderson_drz_renderer.py`
must pass first; it checks normalization, detector-chip orientation, output
centroids and gross PSF shape. `summarize_m31_independent_psf.py` then enforces
the four-method baseline-clear common denominator and reports Wilson intervals,
paired bootstrap recovery differences and exact McNemar tests. The renderer
projects the library PSF through three accepted FLC WCS solutions, but does not
reproduce the complete AstroDrizzle kernel or correlated-noise process.

`parameter_sensitivity_audit.py` holds the NGC 6752 catalogues fixed while
scanning association radius, and separately scans the common DAO-style
proposal threshold. It distinguishes simulation precision from real-catalogue
match lower bounds and reports the result in the supplementary sensitivity
tables.

`angst_non_globular_baseline.py` evaluates DAO, SEP, Photutils, AstroCFR ePSF,
and an AstroCFR-proposal/Photutils-measurement hybrid on the same official ANGST
F814W reference images for M81-DEEP and the dwarf galaxy NGC 2976. The finite
DOLPHOT-derived GST catalogue is a comparison catalogue rather than exhaustive
truth, so unmatched detections are not labelled false positives.

`phat_real_catalogue_benchmark.py` applies the same five branches to a real
PHAT M31 F475W image crop and evaluates against the held-out PHAT v2 field
catalogue. It automatically chooses the densest valid 1200 x 1200 crop from a
DRZ product or an available FLC science extension. The PHAT catalogue is finite
and DOLPHOT-derived, so the reported quantity is catalogue recovery rather than
blind precision or exhaustive completeness.

`real_field_4plus10_benchmark.py` is the registered coordinator for four
original CSST simulated chips plus ten real archive fields. It audits image,
catalogue, and all-method result readiness, aggregates completed real runs, and
excludes the morphology simulations from the `+10` count by construction.

The complementary fixed-truth Galactic-centre-like, thin-disk-like, and dwarf-
galaxy-like simulations live in
`../simulation/astrophysical_scene_benchmark.py`. They test morphology coverage
and method trade-offs; they do not replace real-field or multi-exposure tests
and are not included in the ten-field observational sample.
