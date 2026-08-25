# External method audit

`CSST_PSFNet` is an external MIT-licensed checkout used only for an interface audit. It is ignored by version control; reproduce the checkout with:

```text
git clone --depth 1 https://github.com/P-spec99/CSST_PSFNet.git external/CSST_PSFNet
```

The audit script is `experiments/simulation/audit_csst_psfnet_interface.py`. The current CSST-like AstroCFR FITS files contain an `IMAGE` HDU only, whereas CSST-PSFNet expects labelled `STARS_XX`, `PSFS_XX`, and `METADATA_XX` HDUs. The official Challenge 11 data description likewise provides images and bright-star catalogues, not PSF-truth labels. No checkpoint is distributed in the repository. Training on interpolated science-image cutouts as pseudo-labels would be invalid, so no PSF accuracy number is reported.

DOLPHOT 2.1 base/ACS sources, ACS F606W PSFs, PAM files, and all FLC working
copies are downloaded or created on demand by the multi-epoch audit and are
not vendored.  A Windows-native direct-MinGW compilation and five-FLC joint
run have been demonstrated; the reproducible evaluator is
`experiments/hst/evaluate_dolphot_joint_ngc6397.py`.  The present NGC 6397
pilot has inadequate inter-exposure registration for a manuscript precision
comparison, so its output remains an ignored diagnostic.  See
`results/hst_multiepoch_backend_audit.md`.

The non-globular expansion FLC files are likewise external and are not
included in the repository package. Their exact MAST URIs, sizes, SHA-256
hashes, and ACS/WFC header audit are recorded in
`data/non_globular_flc_manifest.csv` and
`results/non_globular_flc_header_audit.json`. The initial six-field WCS precheck is in
`results/non_globular_wcs_shift_audit.json`; GR8 and Tarantula are retained as
downloaded diagnostics but are not admitted to a DOLPHOT comparison until
source-based registration passes the sub-pixel gate. The registered evidence
target is ten real fields; `results/real_field_4plus10/readiness.csv` separates
missing images, missing catalogues, and incomplete method comparisons.

The independent M31 artificial-star audit uses the official Anderson
ACS/WFC F475W standard PSF library downloaded from
`https://www.stsci.edu/~jayander/HST1PASS/LIB/PSFs/STDPSFs/ACSWFC/STDPSF_ACSWFC_F475W.fits`.
The local file is `external/reference_catalogs/STDPSF_ACSWFC_F475W.fits`
(3,677,760 bytes; SHA-256
`9B3A98844020581FFDF1EEEBB5D4488F03011BC4563C578D19042A76A81B5C82`).
It is a 9 x 10 spatial grid of 101 x 101, four-times-oversampled ACS/WFC
F475W ePSFs. It remains external to the GitHub source archive.
