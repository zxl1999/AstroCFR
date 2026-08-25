# CSST-PSFNet interface audit

`interface_audit.json` records the model-only compatibility check. The model ran successfully on the available CUDA device and returned a `64 × 64` PSF from a `32 × 32` input stamp. The available AstroCFR FITS has only `PRIMARY` and `IMAGE` HDUs; the labelled `STARS_12`, `PSFS_12`, and `METADATA_12` extensions required by CSST-PSFNet are absent. No checkpoint is distributed by the external repository, so no PSF accuracy result is reported.
