#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Five-batch (n=200/stratum) fixed-scene artificial-star HST experiment."""
from __future__ import annotations
import csv, json
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
import hst_acsggct_benchmark as old
import hst_epsf_deblend_artificial_stars as epsf
import hst_unified_baseline_benchmark as bench
import real_data_domain_adaptation as adapt
import real_data_zero_shot_generalization as base

HERE=Path(__file__).resolve().parent;OUT=HERE/'hst_expanded_artificial_ngc6752_results';RNG=np.random.default_rng(20260808)

def scenes(image,psf):
 _,cat=old.read_cluster('ngc6752');x,y,_,quality,_=old.catalog_subsets(cat);ref=np.c_[x[quality],y[quality]];tree=cKDTree(ref);out=[]
 # Five sparse batches x 40 stars = 200 input stars per magnitude/density stratum.
 for mag in (20.,22.):
  for band,(lo,hi) in {'low':(0,1),'high':(3,100000)}.items():
   for batch in range(5):
    pos=[];tries=0
    while len(pos)<40 and tries<100000:
     tries+=1;a,b=RNG.uniform(15,old.CROP_SIZE-15,2);g=np.array([a+old.CROP_X0,b+old.CROP_Y0]);density=len(tree.query_ball_point(g,10))-1
     if lo<=density<=hi and all(np.hypot(a-u,b-v)>12 for u,v in pos):pos.append((a,b))
    if len(pos)<40: raise RuntimeError(f'Only {len(pos)} positions for {mag=} {band=} {batch=}')
    out.append({'mag':mag,'density_band':band,'batch':batch,'positions':np.asarray(pos)})
 return out

def main():
 OUT.mkdir(exist_ok=True);cluster='ngc6752';image,_=old.read_cluster(cluster);sub,rms=base.estimate_background(image)
 pre=base.detect_sources(sub,rms,fwhm=2.0,threshold_sigma=10);mod=adapt.load_pipeline();fwhm=float(np.clip(mod.estimate_psf_fwhm(sub,pre,rms,min_snr=20,max_sources=40),1.5,4.0));rfctx=bench.prepare_wpdc_rf(cluster,sub,rms,fwhm)
 src=base.detect_sources(sub,rms,fwhm=fwhm,threshold_sigma=3);psf,_=epsf.build_epsf(sub,src)
 previous=json.loads((HERE/'hst_epsf_deblend_results'/'hst_epsf_deblend_summary.json').read_text(encoding='utf-8'));zp=next(r['mag_zero_point'] for r in previous['results'] if r['cluster']==cluster)
 rows=[]
 for scene in scenes(sub,psf):
  flux=10**(-.4*(scene['mag']-zp));injected=sub+epsf.render_psf(sub.shape,psf,scene['positions'],np.full(len(scene['positions']),flux));cached_dao=None
  for method in bench.METHODS:
   try:
    if method=='photutils_psf':
     if cached_dao is None: raise RuntimeError('DAO frontend must run first')
     xy=cached_dao
    else:
     (xy,_),_,_=bench.measure(lambda:bench.method_run(method,injected,rms,fwhm,rfctx))
     if method=='dao':cached_dao=xy
    dist,_=cKDTree(xy).query(scene['positions'],k=1) if len(xy) else (np.full(len(scene['positions']),np.inf),None)
    recovered=int((dist<=2).sum());error=''
   except Exception as exc: recovered=0;error=str(exc)
   rows.append({'cluster':cluster,'method':method,'label':bench.LABEL[method],'mag':scene['mag'],'density_band':scene['density_band'],'batch':scene['batch'],'injected':len(scene['positions']),'recovered':recovered,'error':error})
 agg=[]
 for key in sorted({(x['method'],x['mag'],x['density_band']) for x in rows}):
  q=[x for x in rows if (x['method'],x['mag'],x['density_band'])==key];n=sum(x['injected'] for x in q);k=sum(x['recovered'] for x in q);agg.append({'cluster':cluster,'method':key[0],'label':bench.LABEL[key[0]],'mag':key[1],'density_band':key[2],'injected':n,'recovered':k,'recovery':k/n,'recovery_ci95':bench.wilson(k,n)})
 (OUT/'expanded_artificial_summary.json').write_text(json.dumps({'protocol':{'cluster':cluster,'same_scenes':'Each method receives identical fixed scenes generated with seed 20260808.','strata':'V=20/22, low=0-1 and high>=3 quality references within 10 pixels','batches':'5 sparse batches x 40 stars = n=200 per method/stratum','recovery':'any proposal within 2 pixels','ci':'Wilson 95%'},'batches':rows,'aggregate':agg},indent=2),encoding='utf-8')
 with (OUT/'expanded_artificial_recovery.csv').open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=agg[0].keys());w.writeheader();w.writerows(agg)
 print(OUT)
if __name__=='__main__':main()
