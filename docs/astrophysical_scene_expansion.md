# Astrophysical scene expansion

## What this adds

`experiments/simulation/astrophysical_scene_benchmark.py` broadens the fixed-
truth validation beyond globular clusters and M31 with three reproducible
single-image morphology stress tests:

- `galactic_center_like`: a high-density nuclear cusp, steep background,
  patchy differential extinction, and spatially varying elliptical PSF;
- `thin_disk_like`: a vertically concentrated and weakly warped stellar disk
  with a mid-plane dust lane;
- `dwarf_galaxy_like`: an elliptical exponential resolved population with a
  low-surface-brightness background and foreground contamination.

These names intentionally include `-like`. They are controlled simulations,
not claims about any specific observed Galactic-centre, Milky-Way disk, or
dwarf-galaxy field.

## Common comparison protocol

DAOStarFinder, SEP/SExtractor-style extraction, Photutils PSFPhotometry, and
AstroCFR ePSF plus residual deblending receive the identical noisy image. The
scene truth is exhaustive, so both recall and precision are defined. Matching
is greedy one-to-one within two pixels. The renderer uses a spatially varying
elliptical Moffat PSF that is not reused by any recovery branch. Results also
include magnitude- and density-stratified recovery, conditional astrometric and
photometric scatter, wall time, memory, and an exact paired recovery comparison
between AstroCFR ePSF and Photutils.

## Run

```powershell
$env:PYTHONPATH='src/wpdc;experiments/hst'
python experiments/simulation/astrophysical_scene_benchmark.py --scene all
```

For a smoke test:

```powershell
python experiments/simulation/astrophysical_scene_benchmark.py --quick
```

The default outputs are written to `results/astrophysical_scene_benchmark/`.
The scene parameters are disclosed in
`configs/astrophysical_crowded_scenes.json`.

## Evidence boundary

This experiment closes the narrow *morphology coverage* gap at the controlled
simulation level. It does not close the real-field or multi-exposure gaps. The
existing ANGST benchmark supplies independent single-reference-image evidence
for M81-DEEP and the dwarf galaxy NGC 2976, while a valid Galactic-centre and
Milky-Way thin-disk observational claim still requires public images plus an
independently deeper or artificial-star truth set. Multi-exposure processing
remains a separate backend problem.
