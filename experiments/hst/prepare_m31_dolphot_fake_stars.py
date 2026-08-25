#!/usr/bin/env python
"""Prepare a deterministic, image-only artificial-star list for M31 DOLPHOT.

The generated list follows DOLPHOT's native FakeStars format: extension, chip,
reference-frame x/y, and counts for each of the three F475W images.  Local
density strata are determined solely from the completed image-derived DOLPHOT
candidate catalogue, never from PHAT labels.  For ACS, DOLPHOT's FakeStars
reader expects one *VEGAMAG per distinct filter*, not one count per exposure.
Fake mode adds stars in memory
and does not overwrite the original FLC files.
"""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree


def choose_positions(xy, n, lo, hi, rng):
    tree=cKDTree(xy); out=[]; tries=0
    while len(out)<n and tries<500000:
        tries+=1; p=rng.uniform([20,20],[4076,2028])
        d=len(tree.query_ball_point(p,10.0))
        if lo <= d <= hi: out.append((p[0],p[1],d))
    if len(out)<n: raise RuntimeError(f'Only {len(out)}/{n} positions found for density [{lo},{hi}]')
    return out


def main():
    p=argparse.ArgumentParser();p.add_argument('--joint',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--n',type=int,default=10);p.add_argument('--seed',type=int,default=20260812);p.add_argument('--mags',type=float,nargs='+',default=[24.5,26.5])
    a=p.parse_args(); raw=np.loadtxt(a.joint); rng=np.random.default_rng(a.seed)
    # Joint F475W: MAG index 16, RATE index 14, source quality entirely image based.
    good=np.isfinite(raw[:,16])&(raw[:,16]<90)&(raw[:,10]<=2)&(raw[:,5]>=5)&(np.abs(raw[:,6])<=0.3)&(raw[:,9]<=0.5)&(raw[:,24]==0)
    # For ACS, ACSreadfakemag consumes one calibrated fake VEGAMAG per
    # distinct filter and ACSfixfakemag derives detector counts per exposure.
    # The run has only F475W, so no exposure-specific count columns follow.
    records=[]; fakes=[]
    for ext in (1,2):
        xy=raw[good&(raw[:,0].astype(int)==ext),2:4]
        tree=cKDTree(xy); probes=rng.uniform([20,20],[4076,2028],size=(30000,2)); dens=np.array([len(tree.query_ball_point(v,10.0)) for v in probes])
        qlo,qhi=np.quantile(dens,[.25,.75]); bins={'low':(0,int(np.floor(qlo))),'high':(int(np.ceil(qhi)),int(dens.max()))}
        for band,(lo,hi) in bins.items():
            for mag in a.mags:
                for x,y,d in choose_positions(xy,a.n,lo,hi,rng):
                    # one line: extension chip X Y F475W_vegamag
                    fakes.append(f'{ext:d} 1 {x:.5f} {y:.5f} {mag:.5f}')
                    records.append({'extension':ext,'chip':1,'x':x,'y':y,'density_band':band,'magnitude_band':('bright' if mag <= 24.5 else 'faint'),'local_density_within_10px':d,'input_vegamag_f475w':mag})
    a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text('\n'.join(fakes)+'\n',encoding='ascii')
    with a.out.with_suffix('.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=records[0].keys());w.writeheader();w.writerows(records)
    a.out.with_suffix('.json').write_text(json.dumps({'seed':a.seed,'n_per_extension_density_mag':a.n,'mags':a.mags,'count':len(records),'density_definition':'image-only DOLPHOT quality candidates within 10 reference-frame pixels','input_format':'extension chip X Y ACS_F475W_VEGAMAG'},indent=2),encoding='utf-8')
    print(json.dumps({'count':len(records),'output':str(a.out)},indent=2))
if __name__=='__main__':main()
