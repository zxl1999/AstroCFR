#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Controlled HST benchmark: conventional baselines versus WPDC.

Every branch uses the same ACSGGCT 1200x1200 crop, quality reference, 2-pixel
association radius, 200-pixel spatial partitions and injection scenes.  The
reference catalogue is used only to fit WPDC's disclosed target-adaptation
branch on partition 0 / tune its threshold on partition 1; partition 2 remains
untouched until final metrics.
"""
from __future__ import annotations

import csv
import argparse
import json
import math
import threading
import time
from pathlib import Path

import numpy as np
import psutil
import sep
from astropy.table import Table
from photutils.detection import DAOStarFinder
from photutils.psf import CircularGaussianPRF, PSFPhotometry, SourceGrouper
from scipy.spatial import cKDTree

import hst_acsggct_benchmark as old
import hst_epsf_deblend_artificial_stars as wpdc_epsf
import hst_spatial_epsf_joint_pilot as spatial_epsf
import real_data_domain_adaptation as adapt
import real_data_zero_shot_generalization as base


HERE=Path(__file__).resolve().parent
OUT=HERE/'hst_unified_baseline_results'
METHODS=('dao','sep','photutils_psf','wpdc_rf','wpdc_epsf_deblend','wpdc_spatial_epsf_joint')
LABEL={'dao':'DAOStarFinder','sep':'SEP/SExtractor-style','photutils_psf':'Photutils PSFPhotometry',
       'wpdc_rf':'WPDC original (target-adapted RF)','wpdc_epsf_deblend':'WPDC ePSF + residual deblend',
       'wpdc_spatial_epsf_joint':'WPDC spatial ePSF + joint fit'}
RNG=np.random.default_rng(20260807)


def wilson(k,n,z=1.96):
    if n==0:return [None,None]
    p=k/n;den=1+z*z/n;mid=(p+z*z/(2*n))/den;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [float(max(0,mid-half)),float(min(1,mid+half))]


def measurement_bootstrap_ci(det_xy, flux, ref_xy, ref_mag, ref_partition, n_boot=1000):
    """Conditional bootstrap CIs for held-out RMS metrics.

    Matching, affine registration, robust clipping, and the training/test
    split are fixed first; the bootstrap resamples the retained held-out
    residuals. This is deliberately labelled conditional, not a CI for the
    entire catalogue-construction procedure.
    """
    matched, ridx = old.one_to_one(det_xy, ref_xy)
    chosen = np.where(matched)[0]
    if len(chosen) < 20: return {}
    ri = ridx[matched]; train = ref_partition[ri] != 2; test = ref_partition[ri] == 2
    if train.sum() < 10 or test.sum() < 5: return {}
    coeff = old.fit_affine(det_xy[chosen][train], ref_xy[ri][train])
    delta = old.apply_affine(det_xy[chosen][test], coeff) - ref_xy[ri][test]
    radial = np.sqrt(np.sum(delta**2, axis=1)); med=np.median(radial);mad=1.4826*np.median(np.abs(radial-med))
    radial=radial[radial <= med + max(3*mad,.05)]
    inst=-2.5*np.log10(np.maximum(np.asarray(flux,float),1e-6));zp=np.median(ref_mag[ri][train]-inst[chosen][train])
    mr=inst[chosen][test]+zp-ref_mag[ri][test];mmed=np.median(mr);mm=1.4826*np.median(np.abs(mr-mmed));mr=mr[np.abs(mr-mmed)<=max(3*mm,.03)]
    rng=np.random.default_rng(20260807+len(det_xy));ast=[];phot=[]
    for _ in range(n_boot):
        if len(radial):
            s=rng.choice(radial,len(radial),replace=True);ast.append(float(np.sqrt(np.mean(s*s)/2)*old.PIXEL_SCALE_MAS))
        if len(mr):
            s=rng.choice(mr,len(mr),replace=True);phot.append(float(np.sqrt(np.mean((s-np.mean(s))**2))))
    out={}
    if ast:out['astrometric_rms_mas_ci95']=list(np.percentile(ast,[2.5,97.5]))
    if phot:out['photometric_rms_mag_ci95']=list(np.percentile(phot,[2.5,97.5]))
    out['measurement_ci_method']='conditional residual bootstrap, 1000 resamples'
    return out


def measure(fn):
    proc=psutil.Process();base_rss=proc.memory_info().rss;peak=[base_rss];stop=threading.Event()
    def poll():
        while not stop.wait(.01): peak[0]=max(peak[0],proc.memory_info().rss)
    th=threading.Thread(target=poll,daemon=True);th.start();t=time.perf_counter()
    try: return fn(),time.perf_counter()-t,max(0,peak[0]-base_rss)/1024**2
    finally: stop.set();th.join()


def dao(image,rms,fwhm):
    tab=base.detect_sources(image,rms,fwhm=fwhm,threshold_sigma=3.0)
    if len(tab)==0:return np.empty((0,2)),np.empty(0)
    return np.c_[np.asarray(tab['xcentroid'],float),np.asarray(tab['ycentroid'],float)],np.asarray(tab['flux'],float)


def sep_detect(image,rms):
    arr=np.ascontiguousarray(image.astype(np.float32));b=sep.Background(arr)
    # The default SEP sub-object cap is too small for the deliberately dense
    # NGC 1851 crop.  Raising it is a disclosed implementation setting, not a
    # reference-aware tuning step, and prevents an avoidable baseline crash.
    sep.set_sub_object_limit(100000)
    objs=sep.extract(arr-b.back(),3.0*b.globalrms,minarea=5,deblend_nthresh=32,deblend_cont=0.005)
    return (np.c_[objs['x'],objs['y']],np.asarray(objs['flux'],float)) if len(objs) else (np.empty((0,2)),np.empty(0))


def photutils_psf(image,rms,fwhm):
    finder=DAOStarFinder(fwhm=fwhm,threshold=3*rms,sharpness_range=(0.05,2.0),roundness_range=(-1.0,1.0),exclude_border=True)
    phot=PSFPhotometry(CircularGaussianPRF(fwhm=fwhm),fit_shape=(9,9),finder=finder,grouper=SourceGrouper(min_separation=2.0),aperture_radius=3.0,fitter_maxiters=30,group_warning_threshold=1000,progress_bar=False)
    tab=phot(image)
    if len(tab)==0:return np.empty((0,2)),np.empty(0)
    good=np.isfinite(tab['x_fit'])&np.isfinite(tab['y_fit'])&np.isfinite(tab['flux_fit'])&(tab['flux_fit']>0)
    return np.c_[np.asarray(tab['x_fit'][good],float),np.asarray(tab['y_fit'][good],float)],np.asarray(tab['flux_fit'][good],float)


def prepare_wpdc_rf(cluster,image,rms,fwhm):
    mod=adapt.load_pipeline();cache=np.load(base.OUT_DIR/'simulation_training_features.npz',allow_pickle=False)
    frozen, frozen_thr, _=base.fit_frozen_classifier(cache['X'],cache['y'],cache['groups'])
    src=base.detect_sources(image,rms,fwhm=fwhm,threshold_sigma=3.0);X,_=mod._extract_clf_features(src,image,rms);xy=np.c_[src[mod._xy_columns(src)[0]],src[mod._xy_columns(src)[1]]]
    _,cat=old.read_cluster(cluster);x,y,measured,quality,cal=old.catalog_subsets(cat);mxy=np.c_[x[measured],y[measured]]-np.array([old.CROP_X0,old.CROP_Y0])
    match,ri=old.one_to_one(xy,mxy);calmeas=np.asarray(cal[measured],bool);positive=match&(ri>=0)&calmeas[np.maximum(ri,0)]
    near,_=cKDTree(mxy).query(xy,k=1);negative=(near>3)&(~positive);cells=adapt.cell_ids(xy,200)
    clf,thr,_,meta=adapt.fit_target_adaptation(cache['X'],cache['y'],cache['groups'],X,positive,negative,cells,frozen,frozen_thr)
    known=positive|negative;val=known&(cells==1)
    if np.sum(val&positive)>=3 and np.sum(val&negative)>=3:
        p=clf.predict_proba((X-X.mean(0))/np.maximum(X.std(0),1e-8))[val,1]
        t,_=adapt.choose_threshold(positive[val].astype(int),p,target_recall=.98,min_precision=.90)
        if t is not None:thr=t
    return mod,clf,float(thr),fwhm,X.mean(0),np.maximum(X.std(0),1e-8)


def wpdc_rf(image,rms,context):
    mod,clf,thr,fwhm,mean,std=context;src=base.detect_sources(image,rms,fwhm=fwhm,threshold_sigma=3.0)
    if len(src)==0:return np.empty((0,2)),np.empty(0)
    X,flux=mod._extract_clf_features(src,image,rms);p=clf.predict_proba((X-mean)/std)[:,1];xc,yc=mod._xy_columns(src);keep=p>=thr
    return np.c_[np.asarray(src[xc],float)[keep],np.asarray(src[yc],float)[keep]],np.asarray(flux,float)[keep]


def wpdc_deblend(image,rms,fwhm):
    src=base.detect_sources(image,rms,fwhm=fwhm,threshold_sigma=3.0)
    if len(src)==0:return np.empty((0,2)),np.empty(0)
    initial=np.c_[src['xcentroid'],src['ycentroid']];psf,_=wpdc_epsf.build_epsf(image,src);det,_,_,_=wpdc_epsf.residual_candidates(image,rms,psf,initial);fit,flux=wpdc_epsf.fit_catalogue(image,psf,det)
    good=np.isfinite(flux)&(flux>0)
    return fit[good],flux[good]


def wpdc_spatial_epsf_joint(image,rms,fwhm):
    """Recovery-oriented WPDC proposals with quadrant ePSFs and two fit passes.

    Candidate creation remains image-only.  The residual pass is intentionally
    retained so this branch tests measurement refinement rather than silently
    trading away the crowded-field recovery operating point.
    """
    src=base.detect_sources(image,rms,fwhm=2.2,threshold_sigma=3.0)
    if len(src)==0:return np.empty((0,2)),np.empty(0)
    initial=np.c_[src['xcentroid'],src['ycentroid']]
    global_psf,_=wpdc_epsf.build_epsf(image,src)
    det,_,_,_=wpdc_epsf.residual_candidates(image,rms,global_psf,initial)
    grid=spatial_epsf.build_quadrant_psfs(image,src)
    fit,flux=spatial_epsf.fit_catalogue_spatial(image,grid,det,passes=2)
    good=np.isfinite(flux)&(flux>0)
    return fit[good],flux[good]


def method_run(name,image,rms,fwhm,rfctx=None):
    if name=='dao':return dao(image,rms,fwhm)
    if name=='sep':return sep_detect(image,rms)
    if name=='photutils_psf':return photutils_psf(image,rms,fwhm)
    if name=='wpdc_rf':return wpdc_rf(image,rms,rfctx)
    if name=='wpdc_epsf_deblend':return wpdc_deblend(image,rms,fwhm)
    if name=='wpdc_spatial_epsf_joint':return wpdc_spatial_epsf_joint(image,rms,fwhm)
    raise KeyError(name)


def evaluate(cluster,name,xy,flux,elapsed,mem):
    _,cat=old.read_cluster(cluster);x,y,measured,quality,_=old.catalog_subsets(cat)
    ref=np.c_[x[quality],y[quality]];mag=np.asarray(cat['Vvega'],float)[quality];part=old.ref_cells(ref)
    det=xy+np.array([old.CROP_X0,old.CROP_Y0]);test=adapt.cell_ids(xy,200)==2;testref=part==2
    match,_=old.one_to_one(det[test],ref[testref]);allref=np.c_[x[measured],y[measured]];allmatch,_=old.one_to_one(det[test],allref)
    out={'cluster':cluster,'method':name,'label':LABEL[name],'candidates':int(len(xy)),'test_references':int(testref.sum()),
         'test_recovered':int(match.sum()),'test_completeness':float(match.sum()/max(testref.sum(),1)),
         'test_completeness_ci95':wilson(int(match.sum()),int(testref.sum())),
         'test_catalog_match_lower_bound':float(allmatch.sum()/max(test.sum(),1)),
         'runtime_s':float(elapsed),'runtime_s_per_mpix':float(elapsed/(old.CROP_SIZE**2)*1e6),'peak_rss_delta_mb':float(mem)}
    for lim in (18,20,22):
        sub=testref&(mag<=lim);m,_=old.one_to_one(det[test],ref[sub]);out[f'recall_v_le_{lim}']=float(m.sum()/max(sub.sum(),1));out[f'n_v_le_{lim}']=int(sub.sum())
    # High-density test subset: >=3 official quality sources within 10 px.
    tree=cKDTree(ref);dens=np.array([len(tree.query_ball_point(p,10))-1 for p in ref]);sub=testref&(mag<=20)&(dens>=3);m,_=old.one_to_one(det[test],ref[sub]);out['high_density_v20_recall']=float(m.sum()/max(sub.sum(),1));out['high_density_v20_n']=int(sub.sum());out['high_density_v20_ci95']=wilson(int(m.sum()),int(sub.sum()))
    metrics=old.measurement_metrics(det,flux,np.ones(len(det),bool),ref,mag,part);out.update(metrics)
    out.update(measurement_bootstrap_ci(det,flux,ref,mag,part))
    return out


def inject_scenes(cluster,image,psf,mag_zero):
    _,cat=old.read_cluster(cluster);x,y,_,quality,_=old.catalog_subsets(cat);ref=np.c_[x[quality],y[quality]];tree=cKDTree(ref);scenes=[]
    for mag in (20.,22.):
        for band,(lo,hi) in {'low':(0,1),'high':(3,10_000)}.items():
            for batch in range(2):
                pos=[];attempts=0
                while len(pos)<20 and attempts<20000:
                    attempts+=1;a,b=RNG.uniform(15,old.CROP_SIZE-15,2);g=np.array([a+old.CROP_X0,b+old.CROP_Y0]);d=len(tree.query_ball_point(g,10))-1
                    if lo<=d<=hi and all(np.hypot(a-u,b-v)>12 for u,v in pos):pos.append((a,b))
                p=np.asarray(pos);flux=10**(-.4*(mag-mag_zero));inj=image+wpdc_epsf.render_psf(image.shape,psf,p,np.full(len(p),flux))
                scenes.append({'mag':mag,'density_band':band,'batch':batch,'positions':p,'image':inj})
    return scenes


def artificial(cluster,image,rms,fwhm,rfctx,psf,mag_zero):
    rows=[]
    for scene in inject_scenes(cluster,image,psf,mag_zero):
        cached_dao=None
        for name in METHODS:
            try:
                if name=='photutils_psf' and cached_dao is not None:
                    # PSFPhotometry receives the identical DAO finder output as
                    # its initial candidate list; recovery is therefore shared.
                    xy=cached_dao;elapsed=0.0;mem=0.0
                else:
                    (xy,_),elapsed,mem=measure(lambda:method_run(name,scene['image'],rms,fwhm,rfctx))
                    if name=='dao': cached_dao=xy
            except Exception: xy=np.empty((0,2));elapsed=float('nan');mem=float('nan')
            if len(xy):dist,_=cKDTree(xy).query(scene['positions'],k=1);rec=int((dist<=2).sum())
            else:rec=0
            rows.append({'cluster':cluster,'method':name,'mag':scene['mag'],'density_band':scene['density_band'],'batch':scene['batch'],'injected':len(scene['positions']),'recovered':rec,'runtime_s':elapsed,'peak_rss_delta_mb':mem})
    return rows


def main():
    global OUT
    args=argparse.ArgumentParser();args.add_argument('--skip-artificial',action='store_true');args.add_argument('--output-dir',default='hst_unified_baseline_results');opts=args.parse_args()
    OUT=HERE/opts.output_dir
    OUT.mkdir(exist_ok=True);results=[];injections=[]
    for cluster in old.CLUSTERS:
        image,cat=old.read_cluster(cluster);sub,rms=base.estimate_background(image);pre=base.detect_sources(sub,rms,fwhm=2.0,threshold_sigma=10);mod=adapt.load_pipeline();fwhm=float(np.clip(mod.estimate_psf_fwhm(sub,pre,rms,min_snr=20,max_sources=40),1.5,4.0))
        print('prepare RF',cluster);rfctx=prepare_wpdc_rf(cluster,sub,rms,fwhm)
        # Same empirical PSF and injection zero point as v8, derived without
        # using injection truth.
        src=base.detect_sources(sub,rms,fwhm=fwhm,threshold_sigma=3);epsf,_=wpdc_epsf.build_epsf(sub,src)
        _,_,_,_,zp=wpdc_epsf.run_cluster(cluster)[:5] if False else (None,None,None,None,None)
        # Reproduce the v8 zero-point convention from the saved summary.
        mag_zero=None
        if not opts.skip_artificial:
            previous=json.loads((HERE/'hst_epsf_deblend_results'/'hst_epsf_deblend_summary.json').read_text(encoding='utf-8'))
            known=[r['mag_zero_point'] for r in previous['results'] if r['cluster']==cluster]
            if known: mag_zero=known[0]
            else:
                # Fit an evaluation-only magnitude zero point on non-test
                # quality matches for a previously unseen ACSGGCT cluster.
                fit0,flux0=wpdc_deblend(sub,rms,fwhm);_,cat0=old.read_cluster(cluster);x0,y0,_,q0,_=old.catalog_subsets(cat0);ref0=np.c_[x0[q0],y0[q0]];part0=old.ref_cells(ref0);ma0=np.asarray(cat0['Vvega'],float)[q0];match0,ri0=old.one_to_one(fit0+np.array([old.CROP_X0,old.CROP_Y0]),ref0);tr=(part0[ri0[match0]]!=2)&np.isfinite(flux0[match0])&(flux0[match0]>0);mag_zero=float(np.median(ma0[ri0[match0][tr]]+2.5*np.log10(flux0[match0][tr])))
        for name in METHODS:
            print(cluster,name)
            try:(xy,flux),elapsed,mem=measure(lambda:method_run(name,sub,rms,fwhm,rfctx));results.append(evaluate(cluster,name,xy,flux,elapsed,mem))
            except Exception as exc:results.append({'cluster':cluster,'method':name,'label':LABEL[name],'error':str(exc)})
        if not opts.skip_artificial:
            injections.extend(artificial(cluster,sub,rms,fwhm,rfctx,epsf,mag_zero))
    # Aggregate artificial recovery and its binomial Wilson interval.
    agg=[]
    for key in sorted({(r['cluster'],r['method'],r['mag'],r['density_band']) for r in injections}):
        rows=[r for r in injections if (r['cluster'],r['method'],r['mag'],r['density_band'])==key];n=sum(r['injected'] for r in rows);k=sum(r['recovered'] for r in rows);agg.append({'cluster':key[0],'method':key[1],'mag':key[2],'density_band':key[3],'injected':n,'recovered':k,'recovery':k/max(n,1),'recovery_ci95':wilson(k,n)})
    payload={'protocol':{'crop':'same central 1200x1200 ACSGGCT crop for every method','association_radius_px':2,'spatial_protocol':'cells 0/1/2: WPDC fitting/threshold/final test; baselines use no labels','artificial_stars':'same fixed scenes for all methods; 2x20 stars per magnitude/density stratum','ci':'Wilson 95% binomial confidence interval','photutils_artificial_detection':'shares the identical DAOStarFinder candidate frontend; PSF fitting affects measurement, not proposal recovery','spatial_epsf_joint':'quadrant image-only empirical PSFs and two neighbour-aware coordinate/flux fitting passes; it remains a separate operating point until the full controlled run is assessed'},'results':results,'artificial_batches':injections,'artificial_aggregate':agg}
    (OUT/'hst_unified_baseline_summary.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    with (OUT/'hst_unified_baseline_results.csv').open('w',newline='',encoding='utf-8') as f:
        fields=sorted({k for r in results for k in r});w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(results)
    if agg:
        with (OUT/'hst_unified_artificial_recovery.csv').open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=agg[0].keys());w.writeheader();w.writerows(agg)
    print(OUT)


if __name__=='__main__':main()
