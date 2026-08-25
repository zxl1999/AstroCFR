#!/usr/bin/env python
"""Single-stack method benchmark on PHAT M31 B21-F15 F475W.

All methods receive the same 1200x1200 DRZ SCI crop and the same deterministic
artificial-star scenes.  This is a single-stack stratum and is not presented as
an input-identical comparison to the three-FLC DOLPHOT backend.
"""
from __future__ import annotations
import csv,json,math,sys,time,threading
from pathlib import Path
import numpy as np, psutil, sep
from astropy.io import fits
from photutils.detection import DAOStarFinder
from photutils.psf import CircularGaussianPRF,PSFPhotometry,SourceGrouper
from scipy.spatial import cKDTree

HERE=Path(__file__).resolve().parent;REPO=HERE.parents[1];sys.path.insert(0,str(REPO/'src'/'wpdc'))
import real_data_zero_shot_generalization as base
import hst_epsf_deblend_artificial_stars as epsf

OUT=REPO/'results'/'non_globular_runs'/'m31_b21_f15'/'single_stack_common_scene'
METHODS=('dao','sep','photutils','astrocfr_epsf')

def wilson(k,n,z=1.96):
 p=k/n;d=1+z*z/n;m=(p+z*z/(2*n))/d;h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d;return [max(0,m-h),min(1,m+h)]
def measure(fn):
 pr=psutil.Process();b=pr.memory_info().rss;pk=[b];stop=threading.Event()
 def poll():
  while not stop.wait(.01):pk[0]=max(pk[0],pr.memory_info().rss)
 t=threading.Thread(target=poll,daemon=True);t.start();s=time.perf_counter()
 try:return fn(),time.perf_counter()-s,(pk[0]-b)/1024**2
 finally:stop.set();t.join()
def dao(im,rms,fwhm):
 t=DAOStarFinder(fwhm=fwhm,threshold=3*rms,exclude_border=True)(im)
 return np.empty((0,2)) if t is None else np.c_[t['xcentroid'],t['ycentroid']]
def sepf(im,rms):
 a=np.ascontiguousarray(im.astype('f4'));sep.set_sub_object_limit(100000);o=sep.extract(a,3*rms,minarea=5,deblend_nthresh=32,deblend_cont=.005)
 return np.empty((0,2)) if len(o)==0 else np.c_[o['x'],o['y']]
def phot(im,rms,fwhm):
 p=PSFPhotometry(CircularGaussianPRF(fwhm=fwhm),fit_shape=(9,9),finder=DAOStarFinder(fwhm=fwhm,threshold=3*rms,exclude_border=True),grouper=SourceGrouper(2),aperture_radius=3,progress_bar=False);t=p(im)
 g=np.isfinite(t['x_fit'])&np.isfinite(t['y_fit'])&(t['flux_fit']>0);return np.c_[t['x_fit'][g],t['y_fit'][g]]
def run(method,im,rms,fwhm,psf):
 if method=='dao':return dao(im,rms,fwhm)
 if method=='sep':return sepf(im,rms)
 if method=='photutils':return phot(im,rms,fwhm)
 s=base.detect_sources(im,rms,fwhm=fwhm,threshold_sigma=3);ini=np.empty((0,2)) if len(s)==0 else np.c_[s['xcentroid'],s['ycentroid']]
 if not len(ini):return ini
 det,_,_,_=epsf.residual_candidates(im,rms,psf,ini);return det
def main():
 OUT.mkdir(parents=True,exist_ok=True);p=REPO/'external'/'non_globular_fields'/'m31_b21_f15'/'phat_f475w_drz.fits'
 with fits.open(p,memmap=True) as h: im=np.asarray(h[1].data[1520:2720,1500:2700],float)
 sub,rms=base.estimate_background(im); src=base.detect_sources(sub,rms,fwhm=2.2,threshold_sigma=3); psf,npsf=epsf.build_epsf(sub,src)
 # Use the same 800 star design cardinality and magnitude/density labels as the
 # multi-FLC experiment; positions are deterministic within this stack crop.
 rng=np.random.default_rng(20260812); existing=np.c_[src['xcentroid'],src['ycentroid']];tree=cKDTree(existing);probes=rng.uniform(20,1180,(30000,2));dens=np.array([len(tree.query_ball_point(v,10)) for v in probes]);qlo,qhi=np.quantile(dens,[.25,.75]);bins={'low':(0,int(np.floor(qlo))),'high':(int(np.ceil(qhi)),int(dens.max()))}
 zp=32.47626495 # registered F475W scale from accepted joint run; scene amplitude only
 scenes=[]
 for band,(lo,hi) in bins.items():
  for mag in (24.5,26.5):
   pos=[]
   while len(pos)<200:
    v=rng.uniform(20,1180,2);d=len(tree.query_ball_point(v,10))
    if lo<=d<=hi:pos.append(v)
   scenes.append((band,mag,np.asarray(pos)))
 rows=[]
 # Render flux scale empirically from the DRZ count-rate distribution: infer
 # zero point by matching the registered scene's typical F475W rate convention.
 # ACS F475W VEGAMAG zeropoint is determined from PHAT combined sources after
 # catalogue matching in a separate audit; here use 26.168 as the archived
 # calibration constant for electrons/s and record it explicitly.
 drz_zp=26.168
 for band,mag,pos in scenes:
  flux=10**(-.4*(mag-drz_zp)); inj=sub+epsf.render_psf(sub.shape,psf,pos,np.full(len(pos),flux))
  for m in METHODS:
   try:
    det,sec,rss=measure(lambda:run(m,inj,rms,2.2,psf));dist,_=cKDTree(det).query(pos) if len(det) else (np.full(len(pos),np.inf),None);k=int((dist<=2).sum());err=''
   except Exception as e:k=0;sec=0;rss=0;err=str(e)
   rows.append({'method':m,'density_band':band,'input_mag':mag,'injected':len(pos),'recovered':k,'recovery':k/len(pos),'recovery_ci95':wilson(k,len(pos)),'runtime_s':sec,'peak_rss_delta_mb':rss,'error':err})
 (OUT/'summary.json').write_text(json.dumps({'protocol':{'image':str(p),'crop_xywh':[1500,1520,1200,1200],'seed':20260812,'match_radius_px':2,'drz_zeropoint_vegamag':drz_zp,'psf_stamps':npsf,'comparison_scope':'single-stack methods only; do not call input-identical to multi-FLC DOLPHOT'},'rows':rows},indent=2),encoding='utf-8')
 with (OUT/'summary.csv').open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
 print(json.dumps(rows,indent=2))
if __name__=='__main__':main()
