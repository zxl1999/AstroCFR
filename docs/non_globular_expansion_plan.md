# Registered 4+10 real-field expansion plan

The evidence design is **4+10**: retain the four original CSST simulated chips
and add ten independent **real HST crowded fields**.  The three fixed-truth
Galactic-centre-like, thin-disk-like, and dwarf-galaxy-like simulations are
supplementary stress tests only.  They are not members of the ten-field real
sample and cannot substitute for a missing observational field.

The admission unit is one real field/exposure sequence with a reproducible
1200 x 1200 crop, a spatial hold-out, and an independently produced deeper
stellar catalogue.  Artificial-star injection may be reported as a separate
recovery diagnostic on a real image, but it does not turn a simulated scene
into one of the ten observational cases.

## Registered ten-field sample

The original six-field acquisition is retained as a historical download
milestone, not as the final evidence count.  The submission-strength target is
now fixed at ten real fields spanning several observational families:

| Family | Candidate fields | Public programme / catalogue route | Status |
|---|---:|---|---|
| M31 PHAT outer disk | 3 | HST GO-12055; PHAT field catalogues | F15 image/catalogue available; F10/F18 download pending |
| M33 PHATTER centre/inner disk and disk | 2 | HST GO-14610; PHATTER brick catalogues | download and footprint audit pending |
| LMC Tarantula dense star-forming disk | 2 | HST GO-12939; HTTP catalogue | FLC images available; catalogue mapping pending |
| ANGST spiral/dwarf fields | 3 | HST GO-10915; ANGST reference/GST products | M81 and NGC 2976 benchmarked; GR8 reference pending |

These field labels describe the actual archive pointings.  The current sample
must not be described as a real Milky-Way Galactic-centre or Milky-Way
thin-disk validation unless such pointings and independent catalogues are
added explicitly.

## Admission gates

1. At least two CTE-corrected ACS/WFC FLC exposures in the same filter and
   compatible depth; four or more exposures are preferred. Two-exposure and
   four-plus-exposure fields are reported as separate strata rather than
   pooled as if they had the same measurement redundancy.
2. Registration residual below the predeclared sub-pixel threshold before any
   DOLPHOT/HST1PASS number is computed.
3. A deep, independently produced reference catalogue covering the real image
   crop.  A limited catalogue is not exhaustive truth and cannot support blind
   FDR.  Artificial-star truth is a separate supplementary diagnostic and is
   never used to count a field among the observational ten.
4. The same 1200 x 1200 (or explicitly registered equivalent) science crop,
   association radius, spatial hold-out rule, magnitude/density bins, and
   artificial-star protocol for all methods in that field.
5. Field-level output of completeness/recovery, blind-FDR only when justified,
   conditional astrometric and photometric RMS, CPU time, peak RSS, and all
   registration/quality rejection reasons.

Results will be aggregated by field using hierarchical bootstrap or a
field-level mixed-effects summary.  Pooling millions of stars into one
binomial interval would overstate confidence because stars within a field are
not independent observations.

## Current evidence boundary

The MAST proposal metadata query confirms the existence of ACS/WFC material
for GO-12055, GO-14610, and GO-12939.  This is not yet an accepted science
sample: FLC filenames, same-filter exposure counts, catalogue depth, and
sub-pixel registration must be checked per pointing.  No field is added to
the manuscript until all admission gates pass.

## Initial six-field acquisition audit (12 August 2026)

Thirty-nine native FLC science files were downloaded and SHA-256 verified as
the initial acquisition milestone:
M81-DEEP (8), NGC2976-DEEP (8), GR8 (8), NGC-2070 pointings (5+5), and M31
PHAT B21-F15 (5).  The header audit confirms ACS/WFC for every file, with
F606W/F475W/F555W field-specific filters.  A WCS-only six-point precheck gives
maximum residuals of 0.071 px (M81), 0.072 px (NGC2976), and 0.069 px (M31
PHAT), so these three proceed to source-based registration.  GR8 contains two
approximately 168-pixel outlier pointings and the Tarantula groups show
approximately 14-pixel maximum residuals; they are retained as downloaded
diagnostics but are not eligible for a DOLPHOT comparison until a source-based
registration setup passes the sub-pixel gate.  This six-field milestone is not
the final 4+10 result; four additional real fields and the missing catalogues
must pass the same gates before the manuscript can claim the ten-field study.
