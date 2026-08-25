# Reporting tools

Report builders consume JSON/CSV outputs and render publication tables and figures. They do not modify input data or manuscript source files.

`build_manuscript_v20_introduction_hyperlinks.py` rebuilds the current manuscript from the archived v19 source. It replaces the Introduction and adds internal citation hyperlinks to the ordered References section.

`build_manuscript_v21_adaptation_budget.py` adds the target-adaptation budget section, Table 21, and Fig. 22 to the v20 manuscript using the archived learning-curve summary.

`build_manuscript_v22_figure_layout.py` embeds the reviewed Fig. 18 and Fig. 20 assets into the v21 manuscript without changing any result values, captions, or references.

`build_manuscript_v23_2026_refs_fig1.py` adds the 2026 literature links, rebuilds the first-appearance-ordered bibliography, and embeds the reviewed architecture figure. `draw_fig1_architecture.py` is the standalone Fig. 1 renderer.

`build_manuscript_v28_transformer_ablation.py` adds the controlled simulation-domain RF/CNN/lightweight-Transformer classifier ablation and Table 24 to the v27 diagnostic manuscript.

`build_manuscript_v29_cross_task_audit.py` records the CSST-PSFNet interface audit and the licensing/task-mismatch rationale for not reporting DECaLS/SwinBayesNet as direct numerical baselines.

`build_manuscript_v30_submission_fixes.py` applies the final submission audit: corrected reference metadata, Word-native external links, restored Table 22/Table 24 bookmarks, and canonical repeated-run runtime/RSS values. It writes a new v30 document and leaves v29 unchanged.

`render_astrocfr_manuscript_figures.py` regenerates the publication-facing raster assets whose legends or titles contain the system name. It reads the registered HST/ACS and simulation summaries, maps only display labels to AstroCFR, and writes the reviewed assets to `results/astrocfr_manuscript_figures/`.

`build_manuscript_v31_astrocfr.py` creates the current manuscript v32. It renames visible system labels to AstroCFR, uses `github.com/zxl1999/AstroCFR`, preserves only the historical `src/wpdc` package path for compatibility, adds the density-adaptive routing result and related-work references, and replaces the selected embedded raster figures with the AstroCFR-rendered assets.

`build_manuscript_v33_slim.py` builds the focused v33 submission pair. It preserves all v32 measurements, compresses the main Results section into five themes, moves the nine-classifier comparison and RF/CNN/Transformer ablation to `AstroCFR_Supplementary_Materials_v33.docx`, and renumbers the surviving main-text figures and tables continuously.

`build_manuscript_v34_scalability_audit.py` adds the three-field image-only density-gate negative control and the registered 1/2/4-process tile-scaling result. It also tightens the abstract wording so the original router is explicitly a density-stratified deployment policy rather than an automatic gate.

`build_manuscript_v35_reframed.py` removes the multimedia-system narrative, rewrites the related-work problem map, distinguishes simulation precision from real-catalogue match lower bounds, adds the registered parameter-sensitivity audit, and moves four internal diagnostic panels to the supplement.

`build_manuscript_v42_closed_book_scope.py` builds the current submission pair from v41. It removes the reference-aware 100% simulation value from the abstract, Table 2, and Fig. 2; adds the image-only closed-book injection pilot to Supplementary S9; documents the rejected multi-exposure registration attempt without presenting it as a DOLPHOT result; and keeps the package version and Word figure/table bookmarks synchronized.

`build_manuscript_v43_roi_abstract.py` builds the current pair from v42. It adds closed-book recovery, CPU runtime ratio, and labelled-area/label-count adaptation return to the abstract, while explicitly stating that person-minute annotation cost was not measured and must not be invented.

`experiments/hst/mast_field_inventory.py` inventories candidate non-globular
ACS/WFC programmes through MAST metadata. `download_non_globular_flc.py`
downloads only selected native FLC science files with resumable Range requests
and SHA-256 manifests; `audit_non_globular_flc.py` checks file integrity and
FITS headers. These tools do not admit a field to the paper until the WCS and
source-based registration gates pass.

The same v23 builder now emits v24 with the updated Abstract and the aspect-ratio-corrected two-row architecture figure.
