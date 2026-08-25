# High-density manuscript consistency audit

## Structural checks

- Main manuscript OOXML integrity: pass
- Supplement OOXML integrity: pass
- Main manuscript: 273 paragraphs, 11 tables, 10 embedded figures.
- Supplement: 114 paragraphs, 29 tables, 17 embedded figures.
- Main equations: 3 displayed Word equations with right-aligned numbers plus 1 inline Word equation for the decision vector.
- Supplement Office Math objects: 2 XML instances (two displayed definitions).
- Main Table 8: 13 rows (header + 11 fields + median); repeating header=True; no row split=True.
- Supplement Table S26: 78 rows (same 11-field comparison); repeating header=True; no row split=True.
- Supplement Table S27: 21 rows (four-chip five-branch crop audit); repeating header=True; no row split=True.
- References: 53/53 retained; exact first-citation order=True.
- Citation closure: every retained reference cited=True; first citations strictly ordered=True; all first citations hyperlinked=True.
- Citation target audit: all first citations point to internal References bookmarks=True.

## Logic and wording audit

- The abstract, results, discussion, and conclusion all distinguish the 11-field HST single-image tier from the four-chip CSST registered full-frame tier; no pooled average is claimed.
- The CSST evidence separates the registered full-frame SExtractor/AstroCFR integration audit from the new method-complete controlled-crop audit; the latter is feasibility evidence with small chip-17/18 denominators, not a full-frame SOTA claim.
- The main claim is consistently conditional: spatial-ePSF has higher high-density recovery than Photutils in 11/11 HST fields, lower position RMS in 7/11, and lower magnitude RMS in 8/11.
- Literature-mapped global-ePSF and three-Gaussian dPSF controls are labelled as dense-denominator controls rather than bit-for-bit external-pipeline reproductions.
- HST RF is explicitly protocol-excluded (unvalidated CSST-to-HST transfer versus a separate supervised target-adaptation experiment); the three-field hybrid, DOLPHOT/ALLFRAME, crowdsource, Euclid/VVV, and CSST-PSFNet remain scoped as partial or input-incompatible where applicable.
- Editorial pass removed over-strong universal/SOTA wording and avoids calling catalogue-conditioned recovery blind purity.

## Deprecated-claim scan

- `eight completed`: not found
- `three real HST/ACS globular-cluster fields`: not found
- `all methods are directly comparable`: not found

## Remaining scientific boundary

The manuscript supports a high-density, single-image, catalogue-conditioned operating-point advantage. It does not establish a universal SOTA result, blind purity, or an input-identical multi-exposure DOLPHOT/ALLFRAME comparison.
