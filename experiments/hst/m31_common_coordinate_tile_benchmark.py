#!/usr/bin/env python
"""Common-sky-coordinate DRZ tile benchmark for M31 B21-F15.

The 800 input stars are the same DOLPHOT FakeStars list used on the three
registered FLC exposures. Their sky coordinates are mapped to the PHAT DRZ
frame. Every method receives the same tile and injected scene. A core/halo
tiling scheme prevents edge detections from being counted twice.
"""
from __future__ import annotations
import argparse, csv, json, math, sys, time, threading
from pathlib import Path
import numpy as np
import psutil, sep
from astropy.io import fits
from scipy.spatial import cKDTree
from photutils.detection import DAOStarFinder
from photutils.psf import CircularGaussianPRF, PSFPhotometry, SourceGrouper

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / 'src' / 'wpdc'))
import real_data_zero_shot_generalization as base
import hst_epsf_deblend_artificial_stars as epsf

FIELD = ROOT / 'external/non_globular_fields/m31_b21_f15'
DRZ = FIELD / 'phat_f475w_drz.fits'
SCENE = ROOT / 'results/non_globular_runs/m31_b21_f15/matched_coordinate_scene/mapped_fake_stars.csv'
OUT = ROOT / 'results/non_globular_runs/m31_b21_f15/matched_coordinate_scene/tile_benchmark'
ALL_METHODS = ('dao', 'sep', 'photutils', 'astrocfr_epsf')
ZP = 26.168
TILE = 600
HALO = 15
MATCH = 2.0

def wilson(k, n, z=1.96):
    if n == 0: return [float('nan'), float('nan')]
    p=k/n; d=1+z*z/n; m=(p+z*z/(2*n))/d
    h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [max(0.,m-h), min(1.,m+h)]

def measure(fn):
    p=psutil.Process(); base_rss=p.memory_info().rss; peak=[base_rss]; stop=threading.Event()
    def poll():
        while not stop.wait(.02): peak[0]=max(peak[0],p.memory_info().rss)
    t=threading.Thread(target=poll,daemon=True); t.start(); start=time.perf_counter()
    try: value=fn()
    finally: stop.set(); t.join()
    return value, time.perf_counter()-start, (peak[0]-base_rss)/1024**2

def dao(im,rms):
    t=DAOStarFinder(fwhm=2.2,threshold=3*rms,exclude_border=True)(im)
    return np.empty((0,2)) if t is None else np.c_[t['xcentroid'],t['ycentroid']]
def sepf(im,rms):
    o=sep.extract(np.ascontiguousarray(im.astype('f4')),3*rms,minarea=5,deblend_nthresh=32,deblend_cont=.005)
    return np.empty((0,2)) if len(o)==0 else np.c_[o['x'],o['y']]
def phot(im,rms):
    p=PSFPhotometry(CircularGaussianPRF(fwhm=2.2),fit_shape=(9,9),finder=DAOStarFinder(fwhm=2.2,threshold=3*rms,exclude_border=True),grouper=SourceGrouper(2),aperture_radius=3,progress_bar=False)
    t=p(im); good=np.isfinite(t['x_fit'])&np.isfinite(t['y_fit'])&(t['flux_fit']>0)
    return np.c_[t['x_fit'][good],t['y_fit'][good]]
def run(method, im, rms, psf=None):
    if method=='dao': return dao(im,rms)
    if method=='sep': return sepf(im,rms)
    if method=='photutils': return phot(im,rms)
    s=base.detect_sources(im,rms,fwhm=2.2,threshold_sigma=3)
    initial=np.empty((0,2)) if len(s)==0 else np.c_[s['xcentroid'],s['ycentroid']]
    if not len(initial): return initial
    det,_,_,_=epsf.residual_candidates(im,rms,psf,initial)
    return det

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--methods', nargs='+', choices=ALL_METHODS, default=list(ALL_METHODS))
    parser.add_argument('--max-tiles', type=int, default=0, help='0 means all non-empty core tiles')
    parser.add_argument('--tile-size', type=int, default=TILE)
    args=parser.parse_args(); methods=tuple(args.methods); tile_size=int(args.tile_size)
    OUT.mkdir(parents=True,exist_ok=True)
    rows=list(csv.DictReader(SCENE.open(encoding='utf-8')))
    with fits.open(DRZ,memmap=True) as h:
        full=np.asarray(h[1].data,dtype=float)
    ny,nx=full.shape
    xy=np.array([[float(r['drz_x']),float(r['drz_y'])] for r in rows])
    results=[]; tile_count=0
    for y0 in range(0,ny,tile_size):
      for x0 in range(0,nx,tile_size):
        y1=min(ny,y0+tile_size); x1=min(nx,x0+tile_size)
        core=np.where((xy[:,0]>=x0)&(xy[:,0]<x1)&(xy[:,1]>=y0)&(xy[:,1]<y1))[0]
        if not len(core): continue
        tile_count+=1
        ya=max(0,y0-HALO); xa=max(0,x0-HALO); yb=min(ny,y1+HALO); xb=min(nx,x1+HALO)
        base_tile=full[ya:yb,xa:xb]
        sub,rms=base.estimate_background(base_tile)
        src=base.detect_sources(sub,rms,fwhm=2.2,threshold_sigma=3)
        try: psf,npsf=epsf.build_epsf(sub,src)
        except Exception: psf,npsf=None,0
        near=np.where((xy[:,0]>=xa)&(xy[:,0]<xb)&(xy[:,1]>=ya)&(xy[:,1]<yb))[0]
        local_xy=xy[near]-np.array([xa,ya])
        # A native DOLPHOT FakeStars job evaluates the listed stars one at a
        # time.  The DRZ benchmark instead injects the mapped sparse list once
        # per tile, so it is explicitly a common-coordinate stack experiment,
        # not an input-identical FLC reprocessing.
        pos=local_xy
        mags=np.array([float(rows[i]['input_vegamag_f475w']) for i in near])
        flux=10**(-.4*(mags-ZP))
        inj=sub+epsf.render_psf(sub.shape,psf,pos,flux) if psf is not None else sub
        for method in methods:
          if method=='astrocfr_epsf' and psf is None: continue
          try: det,sec,rss=measure(lambda m=method:run(m,inj,rms,psf));
          except Exception as e:
            results.append({'tile_x0':x0,'tile_y0':y0,'method':method,'input_mag':None,'injected':0,'recovered':0,'error':repr(e)})
            continue
          core_ids=core
          core_pos=xy[core_ids]-np.array([xa,ya])
          core_mags=np.array([float(rows[i]['input_vegamag_f475w']) for i in core_ids])
          dist=cKDTree(det).query(core_pos,k=1)[0] if len(det) and len(core_pos) else np.full(len(core_pos),np.inf)
          for mag in sorted(set(core_mags)):
            use=core_mags==mag; ok=dist[use]<=MATCH; k=int(ok.sum()); errs=dist[use][ok]
            results.append({'tile_x0':x0,'tile_y0':y0,'method':method,'input_mag':float(mag),'injected':int(use.sum()),'recovered':k,'recovery':k/max(1,int(use.sum())),'recovery_ci95':wilson(k,int(use.sum())),'position_rms_px':float(np.sqrt(np.mean(errs**2))) if len(errs) else None,'runtime_s':sec,'peak_rss_delta_mb':rss,'psf_stamps':npsf,'error':''})
        print(f'tile {tile_count}: core={len(core)} nearby={len(near)}', flush=True)
        if args.max_tiles and tile_count >= args.max_tiles: break
      if args.max_tiles and tile_count >= args.max_tiles: break
    # Aggregate by method and magnitude; Wilson intervals are descriptive and
    # do not treat stars from the same tile as independent field replicates.
    agg=[]
    for method in methods:
      for mag in sorted(set(float(r['input_vegamag_f475w']) for r in rows)):
        q=[r for r in results if r['method']==method and r.get('input_mag')==mag and not r.get('error')]
        n=sum(int(r['injected']) for r in q); k=sum(int(r['recovered']) for r in q); vals=[]
        for r in q:
          if r.get('position_rms_px') is not None: vals.append(float(r['position_rms_px']))
        agg.append({'method':method,'input_mag':mag,'injected':n,'recovered':k,'recovery':k/max(1,n),'recovery_ci95_wilson':wilson(k,n),'tile_count':len(q),'tile_position_rms_median_px':float(np.median(vals)) if vals else None,'runtime_s_sum':sum(float(r.get('runtime_s',0)) for r in q),'peak_rss_delta_mb_max':max([float(r.get('peak_rss_delta_mb',0)) for r in q] or [0])})
    payload={'protocol':{'scene':'DOLPHOT FakeStars coordinates mapped via FLC celestial WCS to PHAT DRZ','drz':str(DRZ),'tile_core_px':tile_size,'halo_px':HALO,'match_radius_px':MATCH,'zeropoint_vegamag':ZP,'methods':methods,'important_scope':'same sky coordinates and same DRZ injection per method; FLC FakeStars and DRZ injection are different image domains, so this is not pixel-identical end-to-end FLC comparison','tile_count':tile_count},'aggregate':agg,'tile_rows':results}
    (OUT/'summary.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    with (OUT/'summary.csv').open('w',newline='',encoding='utf-8') as f:
      w=csv.DictWriter(f,fieldnames=agg[0].keys());w.writeheader();w.writerows(agg)
    print(json.dumps({'tile_count':tile_count,'aggregate':agg},indent=2))
if __name__=='__main__': main()
