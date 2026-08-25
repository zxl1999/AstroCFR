"""Reference-free candidate-quality flags for AstroCFR catalogues.

The flags are intentionally derived from image/candidate quantities only.  They
are deployment metadata, not truth-catalogue precision labels or calibrated
posterior probabilities.
"""
from __future__ import annotations

import numpy as np

LOW_SNR = np.uint16(1)
POOR_PSF_FIT = np.uint16(2)
CROWDED = np.uint16(4)
BRIGHT_CORE_NEARBY = np.uint16(8)
RESIDUAL_DEBLEND = np.uint16(16)
CLASSIFIER_UNCERTAIN = np.uint16(32)

BIT_DEFINITION = {
    int(LOW_SNR): "snr < 5",
    int(POOR_PSF_FIT): "psf_fit_quality > 3",
    int(CROWDED): "at least 3 neighbours within 10 pixels",
    int(BRIGHT_CORE_NEARBY): "within 10 pixels of an image-derived bright core",
    int(RESIDUAL_DEBLEND): "candidate added or displaced by residual deblending",
    int(CLASSIFIER_UNCERTAIN): "0.2 < classifier_probability < 0.8",
}


def build_quality_bitmask(
    snr,
    psf_fit_quality,
    neighbour_count_10px,
    saturation_neighbour_flag,
    deblend_flag,
    classifier_probability,
):
    """Return the documented uint16 bitmask for one catalogue or candidate set."""
    snr = np.asarray(snr, float)
    psf_fit_quality = np.asarray(psf_fit_quality, float)
    neighbour_count_10px = np.asarray(neighbour_count_10px)
    saturation_neighbour_flag = np.asarray(saturation_neighbour_flag, bool)
    deblend_flag = np.asarray(deblend_flag, bool)
    classifier_probability = np.asarray(classifier_probability, float)
    bitmask = np.zeros(snr.shape, dtype=np.uint16)
    bitmask |= (snr < 5).astype(np.uint16) * LOW_SNR
    bitmask |= (psf_fit_quality > 3).astype(np.uint16) * POOR_PSF_FIT
    bitmask |= (neighbour_count_10px >= 3).astype(np.uint16) * CROWDED
    bitmask |= saturation_neighbour_flag.astype(np.uint16) * BRIGHT_CORE_NEARBY
    bitmask |= deblend_flag.astype(np.uint16) * RESIDUAL_DEBLEND
    uncertain = np.isfinite(classifier_probability) & (classifier_probability > .2) & (classifier_probability < .8)
    bitmask |= uncertain.astype(np.uint16) * CLASSIFIER_UNCERTAIN
    return bitmask
