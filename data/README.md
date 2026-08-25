# Data access and provenance

## CSST challenge data

Access the CSST data challenge page:

<https://nadc.china-vo.org/events/CSSTdatachallenge2026/info/challenge_11th>

Do not commit challenge data to this repository. Store downloaded files outside the repository. The checked-file manifest is [`manifest.csv`](manifest.csv); it records the filename, source page/URL, byte count and SHA-256 for the exact files used during development. The CSST challenge package is distributed through the official page rather than a stable per-file URL, so the page URL is recorded for those rows.

## HST/ACS data

The controlled real-image evaluation uses MAST HLSP ACSGGCT v2 F606W products for NGC 6397, NGC 6752 and NGC 1851. The downloader and original manifest are in the project workspace; the submission package includes the NGC 1851 hashes and the existing ACSGGCT manifest.

The catalogue is an evaluation reference, not a competing run. The catalogue match lower bound must not be described as purity.

## Non-globular expansion shortlist

`results/non_globular_field_inventory.json` is a metadata-only MAST inventory
for four candidate programmes: PHAT M31 (GO-12055), PHATTER M33 (GO-14610),
the Hubble Tarantula Treasury (GO-12939), and ANGST (GO-10915). The ten-field
shortlist is in `configs/non_globular_field_candidates.json`; the FLC shortlist
is not itself a scientific result set. FLC products, catalogue depth and independence, and
sub-pixel registration must pass the admission gates in
[`docs/non_globular_expansion_plan.md`](../docs/non_globular_expansion_plan.md)
before any field is downloaded into an external work directory or entered in
the manuscript comparison.

The initial six-field FLC download is recorded in
[`non_globular_flc_manifest.csv`](non_globular_flc_manifest.csv), with byte
counts, SHA-256 hashes, filter and target headers, and direct MAST download
URLs. The corresponding FITS files remain outside the repository under
`external/non_globular_fields/` and are not included in the GitHub ZIP.
The first WCS precheck is archived in
`results/non_globular_wcs_shift_audit.json`; it is a registration diagnostic,
not a DOLPHOT measurement result. The final registered target is ten real
fields. Run `experiments/hst/download_non_globular_flc.py` without `--fields`
to select all ten configuration entries; the original six are only an
acquisition milestone.

## ANGST single-reference-image benchmark

The admitted non-globular single-image comparison uses the official ANGST
F814W reference images and DOLPHOT-derived F606W/F814W GST catalogues for
M81-DEEP and NGC2976-DEEP. Exact filenames, byte counts, and SHA-256 hashes are
recorded in [`angst_reference_manifest.csv`](angst_reference_manifest.csv).
Place these public HLSP files under
`external/non_globular_fields/angst_reference/`, then run:

```powershell
$env:PYTHONPATH='src/wpdc;experiments/hst'
python experiments/hst/angst_non_globular_baseline.py --field all
```

The GST catalogue is finite and is used as an independent comparison catalogue,
not exhaustive truth. Report `catalogue_recovery` rather than blind completeness
or purity. This experiment is separate from the rejected native-FLC
multi-exposure trials.

The Galactic-centre-like, thin-disk-like, and dwarf-galaxy-like morphology
simulations require no external data, but they are supplementary stress tests
only. Their parameters and fixed seeds are in
`configs/astrophysical_crowded_scenes.json`; none is counted among the ten real
archive fields.

## Native multi-exposure FLC audit

The multi-exposure backend audit uses public ACS/WFC F606W CTE-corrected FLC
exposures from MAST programme 10775.  The current registered lists are
`j9l966ssq, j9l966suq, j9l966swq, j9l966syq, j9l966t0q` for NGC 6752 and
`j9l910auq, j9l910avq, j9l910axq, j9l910azq, j9l910b1q, j9l910b3q` for NGC
1851 (all suffixed `_flc.fits`).  Download them from MAST, verify their FITS
checksums, and place them outside version control at
`external/dolphot/flc_multiepoch/<cluster>/`.

Run `experiments/hst/run_dolphot_multiepoch.ps1 -Cluster ngc6752` (or
`ngc1851`) only after installing DOLPHOT ACS PSFs/PAM files.  A comparison is
eligible for reporting only if the run has no material alignment warnings and
uses the registered exposure list, spatial test partition, association rule,
and artificial-star protocol.  It is not valid to compare a native-FLC
artificial-star run with a different stacked-image injection scene as though
the scenes were identical.
