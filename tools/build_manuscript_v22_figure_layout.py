#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Replace reviewed controlled-comparison figures in the v21 manuscript."""
from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "supplementary" / "WPDC_Multimedia_Systems_SCI_manuscript_v21_adaptation_budget.docx"
DEST = ROOT / "supplementary" / "WPDC_Multimedia_Systems_SCI_manuscript_v22_figure_layout.docx"
REPLACEMENTS = {
    "word/media/image18.png": ROOT / "results" / "hst_unified_baseline_figures" / "fig18_controlled_comparison_fixed.png",
    "word/media/image20.png": ROOT / "results" / "hst_unified_baseline_figures" / "fig20_six_branch_comparison_fixed.png",
}


def main():
    for path in REPLACEMENTS.values():
        if not path.exists():
            raise FileNotFoundError(path)
    with NamedTemporaryFile(delete=False, suffix=".docx", dir=DEST.parent) as temporary:
        temp = Path(temporary.name)
    try:
        with ZipFile(SOURCE) as source, ZipFile(temp, "w", ZIP_DEFLATED) as output:
            for info in source.infolist():
                payload = REPLACEMENTS[info.filename].read_bytes() if info.filename in REPLACEMENTS else source.read(info.filename)
                output.writestr(info, payload)
        temp.replace(DEST)
    finally:
        if temp.exists():
            temp.unlink()
    print(DEST)


if __name__ == "__main__":
    main()
