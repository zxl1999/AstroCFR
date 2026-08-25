#!/usr/bin/env python
"""Strict single-injection recovery on M31 PHAT F475W DRZ.

Each mapped DOLPHOT FakeStars coordinate is tested separately.  A trial enters
the denominator only if that *same method* has no baseline detection within
two pixels.  Recovery requires a new detection within two pixels after
injection.  This guards against crediting pre-existing M31 sources to a fake
star.  The test is a DRZ single-image experiment; it is deliberately reported
separately from DOLPHOT's native three-FLC FakeStars experiment.
"""
from __future__ import annotations
import argparse, csv, json, math, sys, time, threading
from pathlib import Path
import numpy as np
import psutil, sep
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from photutils.detection import DAOStarFinder
from photutils.psf import CircularGaussianPRF, PSFPhotometry, SourceGrouper
from scipy.spatial import cKDTree

HERE=Path(__file__).resolve().parent; ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT/'src'/'wpdc'))
import real_data_zero_shot_generalization as base
import hst_epsf_deblend_artificial_stars as epsf
from anderson_drz_injection import AndersonDRZRenderer

SCENE=ROOT/'results/non_globular_runs/m31_b21_f15/matched_coordinate_scene/mapped_fake_stars.csv'
DRZ=ROOT/'external/non_globular_fields/m31_b21_f15/phat_f475w_drz.fits'
OUT=ROOT/'results/non_globular_runs/m31_b21_f15/matched_coordinate_scene/strict_single_injection'
STDPSF=ROOT/'external/reference_catalogs/STDPSF_ACSWFC_F475W.fits'
FLC_DIR=ROOT/'external/non_globular_fields/m31_b21_f15/flc'
FLC_NAMES=('jbex18u6q_flc.fits','jbex18u9q_flc.fits','jbex18ucq_flc.fits')
ZP_VEGA=26.168; HALF=50; PSF_HALF=100; MATCH=2.; FWHM=2.2
# Registered output-grid broadening: the bare WCS-projected Anderson core is
# sharper than the measured PHAT DRZ FWHM because it omits AstroDrizzle's
# resampling kernel.  Sigma=0.55 px was fixed before the final four-method run
# from quadrature matching of the representative projected core to 2.2 px.
ANDERSON_DRZ_BLUR_SIGMA_PX=0.55

def wilson(k,n,z=1.96):
    if not n:return [None,None]
    p=k/n;d=1+z*z/n;m=(p+z*z/(2*n))/d;h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [max(0,m-h),min(1,m+h)]
def detect(method, image, rms, psf=None):
    if method=='dao':
        t=DAOStarFinder(fwhm=FWHM,threshold=3*rms,exclude_border=True)(image)
        return np.empty((0,2)) if t is None else np.c_[t['xcentroid'],t['ycentroid']]
    if method=='sep':
        o=sep.extract(np.ascontiguousarray(image.astype('f4')),3*rms,minarea=5,deblend_nthresh=32,deblend_cont=.005)
        return np.empty((0,2)) if not len(o) else np.c_[o['x'],o['y']]
    if method=='photutils':
        p=PSFPhotometry(CircularGaussianPRF(fwhm=FWHM), fit_shape=(9,9),
                        finder=DAOStarFinder(fwhm=FWHM,threshold=3*rms,exclude_border=True),
                        grouper=SourceGrouper(2), aperture_radius=3, progress_bar=False)
        t=p(image)
        good=np.isfinite(t['x_fit'])&np.isfinite(t['y_fit'])&(t['flux_fit']>0)
        return np.c_[t['x_fit'][good],t['y_fit'][good]]
    if method=='astrocfr_epsf':
        if psf is None: raise ValueError('astrocfr_epsf requires image-derived PSF support')
        src=base.detect_sources(image,rms,fwhm=FWHM,threshold_sigma=3.)
        initial=np.empty((0,2)) if len(src)==0 else np.c_[src['xcentroid'],src['ycentroid']]
        if not len(initial): return initial
        det,_,_,_=epsf.residual_candidates(image,rms,psf,initial)
        return det
    raise ValueError(method)
def monitor(fn):
    p=psutil.Process(); b=p.memory_info().rss; peak=[b]; stop=threading.Event()
    def poll():
        while not stop.wait(.01):peak[0]=max(peak[0],p.memory_info().rss)
    th=threading.Thread(target=poll,daemon=True);th.start();start=time.perf_counter()
    try:v=fn()
    finally:stop.set();th.join()
    return v,time.perf_counter()-start,(peak[0]-b)/1024**2
def nearest_distance(xy, point):
    return float(cKDTree(xy).query(point)[0]) if len(xy) else float('inf')
def local_background(image):
    """Robust local replacement for Background2D on tiny DRZ windows."""
    finite=np.isfinite(image)
    if finite.sum() < image.size*0.7:
        raise ValueError(f'insufficient finite pixels ({finite.sum()}/{image.size})')
    mean,med,std=sigma_clipped_stats(image[finite],sigma=3.,maxiters=5)
    if not np.isfinite(std) or std<=0: raise ValueError(f'invalid local RMS {std}')
    # Invalid DRZ pixels represent no measurement after background removal;
    # filling them with the original positive median would create a false
    # plateau and can seed edge detections.
    return np.where(finite,image-med,0.0),float(std)
def main():
    a=argparse.ArgumentParser();a.add_argument('--method',choices=('dao','sep','photutils','astrocfr_epsf'),required=True);a.add_argument('--limit',type=int,default=0);a.add_argument('--balanced-per-stratum',type=int,default=0,help='deterministic first-N per ext/density/magnitude stratum');a.add_argument('--injection-psf',choices=('empirical','anderson'),default='empirical');args=a.parse_args()
    OUT.mkdir(parents=True,exist_ok=True); rows=list(csv.DictReader(SCENE.open(encoding='utf-8')))
    if args.balanced_per_stratum:
        grouped={}
        for r in rows: grouped.setdefault((r['extension'],r['density_band'],r['input_vegamag_f475w']),[]).append(r)
        rows=[r for key in sorted(grouped) for r in grouped[key][:args.balanced_per_stratum]]
    if args.limit:rows=rows[:args.limit]
    with fits.open(DRZ,memmap=True) as h:full=np.asarray(h[1].data,float)
    renderer=AndersonDRZRenderer(STDPSF,DRZ,[FLC_DIR/n for n in FLC_NAMES],drz_blur_sigma_px=ANDERSON_DRZ_BLUR_SIGMA_PX) if args.injection_psf=='anderson' else None
    output=[]
    for idx,r in enumerate(rows):
        x,y=float(r['drz_x']),float(r['drz_y']); ix,iy=int(round(x)),int(round(y))
        patch=full[iy-HALF:iy+HALF+1,ix-HALF:ix+HALF+1]
        psf_patch=full[iy-PSF_HALF:iy+PSF_HALF+1,ix-PSF_HALF:ix+PSF_HALF+1]
        if patch.shape!=(2*HALF+1,2*HALF+1) or psf_patch.shape!=(2*PSF_HALF+1,2*PSF_HALF+1):continue
        try: sub,rms=local_background(patch)
        except Exception as exc:
            output.append({'fake_id':r['fake_id'],'method':args.method,'status':'excluded_background','reason':repr(exc)});continue
        # Per-location empirical PSF is built image-only.  A fallback Gaussian
        # is not used: insufficient PSF support yields an explicit exclusion.
        try: psf_sub,psf_rms=local_background(psf_patch)
        except Exception as exc:
            output.append({'fake_id':r['fake_id'],'method':args.method,'status':'excluded_psf_background','reason':repr(exc)});continue
        src=base.detect_sources(psf_sub,psf_rms,fwhm=FWHM,threshold_sigma=3)
        try: psf,npsf=epsf.build_epsf(psf_sub,src)
        except Exception as exc:
            output.append({'fake_id':r['fake_id'],'method':args.method,'status':'excluded_psf','reason':repr(exc)});continue
        local=np.array([x-(ix-HALF),y-(iy-HALF)])
        base_det,base_sec,base_mem=monitor(lambda:detect(args.method,sub,rms,psf))
        base_dist=nearest_distance(base_det,local)
        if base_dist<=MATCH:
            output.append({'fake_id':r['fake_id'],'method':args.method,'status':'excluded_preexisting','baseline_nearest_px':base_dist,'density_band':r['density_band'],'extension':r['extension'],'input_vegamag_f475w':r['input_vegamag_f475w'],'psf_stamps':npsf});continue
        mag=float(r['input_vegamag_f475w']); flux=10**(-.4*(mag-ZP_VEGA))
        if renderer is None:
            injected=sub+epsf.render_psf(sub.shape,psf,np.array([local]),np.array([flux]));injector_sum=1.0
        else:
            injected,injector=renderer.add_star(sub,local[0],local[1],x,y,int(r['extension']),flux);injector_sum=float(injector.sum())
        det,sec,mem=monitor(lambda:detect(args.method,injected,rms,psf)); dist=nearest_distance(det,local); ok=dist<=MATCH
        output.append({'fake_id':r['fake_id'],'method':args.method,'status':'eligible','recovered':bool(ok),'injection_nearest_px':dist,'baseline_nearest_px':base_dist,'density_band':r['density_band'],'extension':r['extension'],'input_vegamag_f475w':mag,'injection_psf':args.injection_psf,'injection_psf_sum':injector_sum,'psf_stamps':npsf,'runtime_s':base_sec+sec,'peak_rss_delta_mb':max(base_mem,mem)})
        if (idx+1)%50==0:print(f'{args.method}: {idx+1}/{len(rows)}',flush=True)
    eligible=[r for r in output if r.get('status')=='eligible']; agg=[]
    for mag in sorted({float(r['input_vegamag_f475w']) for r in eligible}):
      for density in ('low','high'):
        q=[r for r in eligible if float(r['input_vegamag_f475w'])==mag and r['density_band']==density];n=len(q);k=sum(bool(r['recovered']) for r in q)
        agg.append({'method':args.method,'input_vegamag_f475w':mag,'density_band':density,'eligible':n,'recovered':k,'recovery':k/n if n else None,'recovery_ci95_wilson':wilson(k,n),'runtime_s_median':float(np.median([r['runtime_s'] for r in q])) if n else None,'rss_delta_mb_max':max([r['peak_rss_delta_mb'] for r in q] or [0])})
    payload={'protocol':{'input_scene':'800 DOLPHOT FakeStars sky coordinates WCS-mapped to PHAT DRZ','image_domain':'single PHAT F475W DRZ; electrons/s','magnitude_scale':'ACS/WFC F475W VEGAMAG zeropoint 26.168, declared conversion assumption','method':args.method,'injection_psf':args.injection_psf,'anderson_source':str(STDPSF) if renderer else None,'anderson_renderer':'official spatial F475W ePSF evaluated through three FLC-to-DRZ WCS mappings; averaged, convolved with a registered Gaussian output-grid kernel (sigma=0.55 px) to match the measured 2.2-px DRZ FWHM, and normalized; full AstroDrizzle kernel/correlated noise not simulated' if renderer else None,'anderson_drz_blur_sigma_px':ANDERSON_DRZ_BLUR_SIGMA_PX if renderer else None,'window_size_px':2*HALF+1,'psf_support_window_px':2*PSF_HALF+1,'balanced_per_extension_density_magnitude':args.balanced_per_stratum,'recovery':'baseline-unmatched source acquires a detection within 2 DRZ px after its one-star injection','important_scope':'not an input-identical comparison to DOLPHOT native multi-FLC FakeStars; use only as strict single-stack recovery evidence'},'aggregate':agg,'rows':output}
    if renderer:renderer.close()
    suffix=args.method+('_anderson' if args.injection_psf=='anderson' else '')
    (OUT/f'{suffix}_summary.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    fields=sorted({k for r in output for k in r})
    with (OUT/f'{suffix}_rows.csv').open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(output)
    print(json.dumps({'method':args.method,'aggregate':agg,'eligible_total':len(eligible),'excluded_preexisting':sum(r.get('status')=='excluded_preexisting' for r in output)},indent=2))
if __name__=='__main__':main()
