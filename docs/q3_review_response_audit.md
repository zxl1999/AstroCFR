# Q3-review response audit

This record separates completed evidence from work that is still required. It
must not be used to relabel a diagnostic as a manuscript result.

Manuscript revision v43 implements this boundary: the reference-aware 100%
simulation value is absent from the abstract, the main simulation table, and
Fig. 2; the image-only closed-book injection pilot is confined to
Supplementary S9; and the failed FLC registration attempt is documented only
as a readiness audit.

The v43 abstract quantifies the practical trade-offs without inventing a human
annotation time: one 2.8%-area calibration tile and its observed positive /
negative candidate counts are reported, while person-minutes are explicitly
identified as unmeasured. It also reports the measured 24.24 versus 0.11
s/MPix CPU ratio. The closed-book pilot still cannot supply a blind precision
or FDR because the available top-1000 challenge catalogue is not certified as
exhaustive truth; a larger exhaustive-scene audit remains a submission gate
for any blind-purity claim.

## 1. Truth-aware simulation assembly

The former simulated `100% precision` result is an oracle-conditioned assembly
metric if the supplied reference catalogue participates in final acceptance.
It is not blind precision. It should be removed from any abstract/highlight
claim and retained, if at all, as a supplementary implementation audit.

`experiments/simulation/blind_simulation_audit.py` provides a closed-book
counter-audit. It writes each withheld-chip candidate catalogue before opening
that chip's `top1000` catalogue. Its `image_only_proposal` branch uses no labels
at all; its `rf_loco` branch trains only on the other three chips. The output
calls the detection-side quantity `catalogue-match fraction`, not blind purity,
because the supplied `top1000` catalogue is not known to be an exhaustive scene
truth catalogue.

The current 3000-pixel central-crop image-only pilot uses 40 injected sources
per available chip/stratum. The pipeline is not given injected positions. The
aggregate recovery is 102/160 (0.638) at injected peak SNR 10 and 143/160
(0.894) at peak SNR 30 in low-density positions; it is 22/120 (0.183) and
91/120 (0.758), respectively, in high-density positions. These are a
truth-free frontend stress test, not a full AstroCFR-catalogue result and not
an acceptable replacement for a full simulated blind-FDR measurement.

## 2. Multi-exposure external backend

The NGC 6752 four-deep-FLC DOLPHOT repeat failed the registered alignment and
PSF acceptance gate. WCS-derived dither shifts recovered thousands of alignment
matches, but the best tested cubic fit still had 0.817-pixel scatter, far above
the required sub-pixel regime. No NGC 1851 run is reported under the same
unresolved condition. `results/hst_multiepoch_backend_audit.md` is the
authoritative negative record. Consequently, no multi-exposure
DOLPHOT/HST1PASS comparison appears in the manuscript.

The next credible experiment requires a FLC sequence and registration setup
that first passes sub-pixel inter-exposure alignment. Only then may the shared
exposure list, spatial hold-out area, and artificial-star protocol be used for
a candidate-prior versus backend comparison.

## 3. Generalization and real blind fields

The current Pan-STARRS1 M31 and Legacy M13 experiments are explicit zero-shot
negative controls. Few-shot target adaptation uses labels in spatially
disjoint tiles and must remain labelled as supervised target adaptation. A
non-cluster field should be added only with an independent sufficiently deep
catalogue or an artificial-star experiment; a shallow Gaia match cannot
establish blind purity in M31 or a bulge field.

## 4. Method contribution boundary

The present evidence supports a calibration-aware candidate-recovery framework,
not a new universal PSF-photometry theory. The concrete path to a stronger
method claim is: image-only AstroCFR candidates and group topology initialise a
multi-exposure, local, Poisson/read-noise weighted, spatial-ePSF joint fit;
the same physical backend is then compared with and without those candidate
priors. The existing single-image differentiable group-fit diagnostic is a
negative pilot and cannot be promoted without multi-field and injection
replication.

## Submission gate

Do not submit the current manuscript as a Q3 multi-exposure or blind-purity
paper. A Q3-level revision needs, at minimum: a fully closed-book simulation
catalogue/FDR evaluation with exhaustive truth; an alignment-passing two-field
multi-FLC backend comparison; and one independently referenced non-cluster
crowded field. Until then, the defensible scope is single-image controlled
candidate recovery with disclosed limits.
