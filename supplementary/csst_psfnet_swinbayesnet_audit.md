# Cross-task Transformer audits

## CSST-PSFNet

The MIT-licensed repository was checked out locally and its model interface was executed on the available GPU. The network has 32,154,727 parameters, accepts a `1 × 32 × 32` star stamp plus CCD and normalized position, and returns a `64 × 64` reconstructed PSF. The AstroCFR CSST-like FITS files provide only an `IMAGE` HDU; they do not contain the required `STARS_XX`, `PSFS_XX`, and `METADATA_XX` labelled extensions. The official Challenge 11 description likewise supplies simulated images and the brightest-1,000-star catalogues, rather than PSF-truth products. No checkpoint is distributed. Consequently, this is recorded as an interface audit rather than an accuracy comparison.

## SwinBayesNet

SwinBayesNet was not copied or vendored because its repository does not declare a usable open-source license and its task is SDSS five-band hot-subdwarf classification. The WPDC repository instead contains an independently implemented lightweight patch Transformer candidate classifier, evaluated under the same simulation candidate protocol. This preserves both licensing and task fairness.
