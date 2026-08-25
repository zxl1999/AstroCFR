# AstroCFR v35 submission-gap audit

## Current evidence already in the manuscript

- Four official CSST Data Challenge 11 simulated chips with registered hashes.
- Identical-image HST/ACS comparison against DAOStarFinder, SEP/SExtractor and
  Photutils PSFPhotometry.
- Three real crowded fields: NGC 6397, NGC 6752 and NGC 1851.
- Spatially held-out simulation-to-real adaptation-budget curves.
- Fixed-scene artificial-star recovery, density/magnitude stratification and
  Wilson 95% intervals.
- Conditional residual-bootstrap intervals for position and magnitude RMS.
- Five-repeat CPU runtime/RSS measurements and a separate CNN CPU/GPU profile.
- RF/CNN/lightweight-Transformer classifier ablation, failure cases and an
  explicitly non-dominating density-adaptive routing operating point.
- A three-field image-only density-gate negative control. Its held-out
  artificial-position high-density sensitivity is not stable across fields,
  so the density router remains explicitly a registered-stratum policy rather
  than an automatic-gating claim.
- 1/2/4-process tile-level scaling of the ePSF+deblend branch on identical
  NGC 6752 tiles: 2.04x and 3.02x speed-up, with the disclosed near-linear
  aggregate-RSS cost and identical candidate totals.

These results are sufficient for the manuscript's bounded system claim. They
do not support universal SOTA, multi-epoch catalogue production, or blind
catalogue-purity claims.

## Additional experiments by priority

1. **Useful but not blocking: replicate the expanded artificial-star protocol
   on NGC 1851 and, where enough crowded locations exist, NGC 6397.** Use the
   same magnitude bins, density rule, 2-pixel association, branch settings and
   injection count. This would strengthen cross-field recovery replication.
2. **High value, high effort: multi-exposure DOLPHOT/HST1PASS/ALLFRAME-style
   comparison.** It requires homogeneous FLC exposure depth, stable
   registration, an independent multi-epoch reference and a common matching
   protocol. It is appropriate for a stronger follow-up paper or a reviewer
   request, not a quick extra baseline.
3. **Useful only with deeper truth: blind false-discovery/purity evaluation.**
   The current HST catalogue-match rate is correctly reported as a lower bound.
   It must not be relabelled as purity without deeper truth or controlled
   simulations containing all sources.

The automatic density-gate audit should not be promoted to a main algorithm
unless a future image-only gate attains stable sensitivity and specificity on
spatially isolated tests in all fields. The current negative result is useful
as a boundary, not as a novelty claim.

## Experiments not recommended for the current claim

- Adding GAN, diffusion or a larger Transformer solely for architectural
  novelty. The controlled Transformer ablation already shows that the RF is the
  stronger candidate-screening operating point under the registered protocol.
- Reporting CSST-PSFNet, SwinBayesNet, DRUID or Astro-RetinaNet as direct
  numerical baselines without a common task, images, truth catalogue and
  released checkpoint. They remain contextual literature comparisons.
- Pooling metrics from incompatible filters, instruments or association rules
  into a SOTA leaderboard.

## Literature status

The v32 bibliography contains 47 references, including 13 works from
2025–2026. The recent set covers CSST source detection, density gating,
geometric distortion, PSF reconstruction and astrometry, persistent-homology
deblending, physics-informed photometry and crowded-field survey practice.
The literature coverage is adequate; further citations should be added only
when they motivate a reproducible experiment or a clearly delimited limitation.
