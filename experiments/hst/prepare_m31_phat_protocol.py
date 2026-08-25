#!/usr/bin/env python
"""Freeze the common M31 test partition and artificial-star scene manifest.

This stage deliberately does not inject or alter science FLC files yet.  It
creates a reproducible, spatially held-out protocol and records whether the
PHAT catalogue is being used as an external lower-bound reference only.
"""
from __future__ import annotations
import argparse, csv, hashlib, json
from pathlib import Path
import numpy as np
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS


def main():
    p=argparse.ArgumentParser();p.add_argument('--flc',type=Path,required=True);p.add_argument('--phat',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--seed',type=int,default=20260812);p.add_argument('--n-per-stratum',type=int,default=100)
    a=p.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    with fits.open(a.flc,memmap=True) as h:
        shape=tuple(int(v) for v in h[1].data.shape); wcs=[WCS(h[i].header,fobj=h).to_header_string()[:0] for i in (1,2)]
    ph=Table.read(a.phat); x=np.asarray(ph['X'],float);y=np.asarray(ph['Y'],float)
    # PHAT reference-frame coordinates cover the detector mosaic, but are not
    # treated as exhaustive truth for completeness.
    rng=np.random.default_rng(a.seed); rows=[]
    for ext in (1,2):
      for density in ('low','high'):
       for magbin in ('bright','faint'):
        for i in range(a.n_per_stratum):
         # deterministic image-only injection locations; avoid 15-pixel edges
         xx=float(rng.uniform(15,shape[1]-15)); yy=float(rng.uniform(15,shape[0]-15))
         rows.append({'extension':ext,'density_band':density,'magnitude_band':magbin,'index':i,'x':xx,'y':yy,'seed':a.seed})
    with (a.out/'artificial_star_scene.csv').open('w',newline='',encoding='utf-8') as f:
      w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
    protocol={'field':'M31 PHAT B21-F15','flc':str(a.flc),'detector_shape':shape,'extensions':[1,2],'match_radius_arcsec':0.1,'spatial_test_partition':'fixed detector quadrants; no PHAT labels used for fitting or threshold selection','reference_catalogue':'PHAT v2 field catalogue; external catalogue-match lower bound only','artificial_scene':{'seed':a.seed,'rows':len(rows),'n_per_extension_density_mag_stratum':a.n_per_stratum,'injection_edge_margin_px':15,'note':'F475W flux assignment and pixel-level injection runner are still pending; this manifest must not be reported as recovery.'}}
    (a.out/'protocol.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8')
    print(a.out)
if __name__=='__main__':main()
