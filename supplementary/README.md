# Supplementary material

Place extended recovery tables, bootstrap diagnostics, runtime repeat logs, data manifests and additional field-level figures here. Large FITS files remain external and are identified by URL and SHA-256.

## Current high-density manuscript pair

`AstroCFR_Crowded_Field_Manuscript_v45_high_density_final.docx` and
`AstroCFR_Supplementary_Materials_v45_high_density_final.docx` are the current
matched pair. They report four registered CSST-like full-frame chips and 11
ACSGGCT HST/ACS F606W fields as separate evidence tiers. The main result is a
conditional single-image high-density comparison: the spatial-ePSF joint branch
has higher V<=20 recovery than Photutils in all 11 ACSGGCT fields, while the
machine-readable matrix retains unavailable and input-incompatible methods
without assigning them zero performance. The corresponding CSV/JSON artefacts
are under `results/acsggct11_csst4_*`, `results/acsggct_all11_baselines/`, and
`results/hst_literature_method_benchmark_all11/`.

Current additions include the environment locks, classifier diagnostics, failure analyses, the v44 validated manuscript pair, and the archived v45 scene-scope draft. The manuscript-facing system name is **AstroCFR (Astronomical Crowded-Field Recovery System)**. Machine-readable outputs remain under `results/`, including the registered ten-real-field readiness table and completed real-image comparisons.

`AstroCFR_Crowded_Field_Manuscript_v45_scene_scope.docx` and its matched supplement are archived drafts, not the current submission pair. Their synthetic Galactic-centre-like, thin-disk-like, and dwarf-galaxy-like scenes remain valid stress tests but cannot be counted as real observational cases. The next revision must use the 4+10 protocol: four original CSST simulated chips plus ten real archive fields, with incomplete fields labelled pending rather than replaced by simulations.

`AstroCFR_Crowded_Field_Manuscript_v44_independent_psf.docx` supersedes v43
for submission. It discloses that the earlier NGC 6752 artificial stars used
the same image-derived empirical PSF family as the AstroCFR recovery branch,
and adds an independent official Anderson F475W PSF experiment on PHAT M31.
`AstroCFR_Supplementary_Materials_v44.docx` is its matched supplement; Section
S10 contains the complete paired table, renderer audit and scope limitations.

`WPDC_Multimedia_Systems_SCI_manuscript_v20_introduction_hyperlinks.docx` introduced the revised Introduction, first-appearance-ordered references, hanging-indent bibliography formatting, and internal hyperlinks for figures, tables, and Introduction citations.

`WPDC_Multimedia_Systems_SCI_manuscript_v21_adaptation_budget.docx` added the three-domain simulation-to-HST target-adaptation budget experiment, its calibration-budget table, and its learning-curve figure.

`WPDC_Multimedia_Systems_SCI_manuscript_v22_figure_layout.docx` replaced Fig. 18 and Fig. 20 with label-collision-free reviewed versions.

`WPDC_Multimedia_Systems_SCI_manuscript_v23_2026_refs_fig1.docx` added four DOI-verified 2026 references, linked them from the Introduction, reordered all 41 references by first appearance, and embedded the redrawn Fig. 1.

`WPDC_Multimedia_Systems_SCI_manuscript_v24_2026_refs_fig1_abstract.docx` additionally updated the Abstract with the two-stage deployment and three-domain calibration-budget results and used the final two-row Fig. 1 layout.
