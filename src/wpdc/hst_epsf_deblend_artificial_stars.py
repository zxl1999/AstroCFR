#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Independent HST ePSF/deblending and artificial-star extension for WPDC.

This experiment deliberately leaves the v7 benchmark untouched.  It derives an
empirical PSF from image-only, isolated stars, applies PSF-weighted centroid and
flux fitting, then uses a residual pass to propose close companions.  The
official ACSGGCT catalogue is used only for the final measurements and for a
catalogue-derived *reporting* magnitude zero point; injected-star positions and
fluxes are generated before each recovery run.
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from astropy.table import Table, vstack
from photutils.detection import DAOStarFinder
from scipy.ndimage import map_coordinates, shift
from scipy.optimize import least_squares, nnls
from scipy.spatial import cKDTree

import hst_acsggct_benchmark as old
import real_data_zero_shot_generalization as base


HERE = Path(__file__).resolve().parent
OUT = HERE / "hst_epsf_deblend_results"
RNG = np.random.default_rng(20260806)
HALF = 6
PSF_SIZE = 2 * HALF + 1


def local_patch(image, x, y, half=HALF):
    ix, iy = int(round(x)), int(round(y))
    if ix-half < 0 or iy-half < 0 or ix+half >= image.shape[1] or iy+half >= image.shape[0]:
        return None
    return image[iy-half:iy+half+1, ix-half:ix+half+1], ix, iy


def build_epsf(image, sources):
    """Median image-derived PSF aligned to each source's DAO centroid."""
    x = np.asarray(sources["xcentroid"], float); y = np.asarray(sources["ycentroid"], float)
    flux = np.asarray(sources["flux"], float)
    tree = cKDTree(np.c_[x, y])
    near, _ = tree.query(np.c_[x, y], k=2)
    order = np.argsort(flux)[::-1]
    stack = []
    for i in order:
        if len(stack) >= 80:
            break
        if not np.isfinite(flux[i]) or flux[i] <= 0 or near[i, 1] < 9:
            continue
        item = local_patch(image, x[i], y[i])
        if item is None:
            continue
        patch, ix, iy = item
        edge = np.r_[patch[0], patch[-1], patch[:, 0], patch[:, -1]]
        patch = patch - np.median(edge)
        if patch[HALF, HALF] <= 0:
            continue
        # shift the fractional centroid to the centre of the stack
        patch = shift(patch, (iy-y[i], ix-x[i]), order=3, mode="nearest", prefilter=True)
        norm = np.sum(np.maximum(patch, 0))
        if norm > 0:
            stack.append(patch / norm)
    if len(stack) < 12:
        raise RuntimeError(f"Only {len(stack)} isolated PSF stamps available")
    psf = np.median(np.stack(stack), axis=0)
    psf = np.maximum(psf, 0); psf /= psf.sum()
    return psf, len(stack)


def psf_values(psf, xx, yy, x0, y0):
    # Coordinates expressed in the empirical-PSF stamp coordinate system.
    return map_coordinates(psf, [yy-y0+HALF, xx-x0+HALF], order=3, mode="constant", cval=0.0)


def fit_one_psf(image, psf, x0, y0, neighbours):
    """Fit a source centroid/flux with nearby positions simultaneously modelled.

    Fluxes are non-negative linear parameters at each centroid update.  Fitting
    a local constant background makes this deliberately conservative in strongly
    structured cluster light.
    """
    item = local_patch(image, x0, y0, half=5)
    if item is None:
        return x0, y0, np.nan
    patch, ix, iy = item
    yy, xx = np.mgrid[iy-5:iy+6, ix-5:ix+6]
    yy = yy.astype(float); xx = xx.astype(float)
    other = np.asarray(neighbours, float)
    def solve(par):
        xc, yc = par
        coords = np.vstack([[xc, yc], other]) if len(other) else np.array([[xc, yc]])
        design = [psf_values(psf, xx, yy, a, b).ravel() for a, b in coords]
        design.append(np.ones(patch.size))
        A = np.column_stack(design)
        # NNLS only for source fluxes; background is unconstrained after removal.
        bkg = np.median(np.r_[patch[0], patch[-1], patch[:, 0], patch[:, -1]])
        coeff, _ = nnls(A[:, :-1], (patch.ravel()-bkg))
        model = A[:, :-1] @ coeff + bkg
        return (model-patch.ravel()), coeff[0]
    def residual(par):
        return solve(par)[0]
    try:
        fit = least_squares(residual, [x0, y0], bounds=([x0-1, y0-1], [x0+1, y0+1]), max_nfev=20)
        _, flux = solve(fit.x)
        return float(fit.x[0]), float(fit.x[1]), float(flux)
    except Exception:
        return x0, y0, np.nan


def fit_catalogue(image, psf, xy):
    tree = cKDTree(xy)
    out_xy = np.empty_like(xy); flux = np.empty(len(xy))
    for i, pos in enumerate(xy):
        ids = tree.query_ball_point(pos, r=6.0)
        others = xy[[j for j in ids if j != i]]
        xf, yf, ff = fit_one_psf(image, psf, pos[0], pos[1], others)
        out_xy[i] = (xf, yf); flux[i] = ff
    return out_xy, flux


def render_psf(image_shape, psf, xy, flux):
    model = np.zeros(image_shape, float)
    for (x, y), f in zip(xy, flux):
        if not np.isfinite(f) or f <= 0:
            continue
        item = local_patch(model, x, y)
        if item is None:
            continue
        _, ix, iy = item
        yy, xx = np.mgrid[iy-HALF:iy+HALF+1, ix-HALF:ix+HALF+1]
        model[iy-HALF:iy+HALF+1, ix-HALF:ix+HALF+1] += f * psf_values(psf, xx, yy, x, y)
    return model


def residual_candidates(image, rms, psf, initial):
    """One residual peak pass, rejecting duplicates closer than one pixel."""
    fit_xy, flux = fit_catalogue(image, psf, initial)
    residual = image - render_psf(image.shape, psf, fit_xy, flux)
    finder = DAOStarFinder(fwhm=2.2, threshold=3.0*rms, sharpness_range=(0.03, 2.5),
                            roundness_range=(-1.5, 1.5), exclude_border=True)
    extra = finder(residual)
    if extra is None or len(extra) == 0:
        return initial, fit_xy, flux, 0
    e = np.c_[np.asarray(extra['xcentroid'], float), np.asarray(extra['ycentroid'], float)]
    dist, _ = cKDTree(initial).query(e, k=1)
    e = e[dist > 1.0]
    # Keep a bounded number of residual candidates; pathological noise peaks do
    # not dominate the high-density experiment.
    if len(e) > 2500:
        e = e[:2500]
    combined = np.vstack([initial, e])
    return combined, fit_xy, flux, len(e)


def evaluate(cluster, detections, fitted_xy, fitted_flux, quality_xy, quality_mag, partition, mag_zero):
    det_global = detections + np.array([old.CROP_X0, old.CROP_Y0])
    fit_global = fitted_xy + np.array([old.CROP_X0, old.CROP_Y0])
    test_ref = partition == 2
    test_det = old.adapt.cell_ids(detections, 200) == 2
    matched, ri = old.one_to_one(det_global[test_det], quality_xy[test_ref])
    result = {
        'cluster': cluster, 'proposals': int(len(detections)), 'test_refs': int(test_ref.sum()),
        'test_recall': float(matched.sum()/max(test_ref.sum(), 1)),
    }
    for limit in (18, 19, 20, 21, 22):
        ref = quality_xy[test_ref & (quality_mag <= limit)]
        match, _ = old.one_to_one(det_global[test_det], ref)
        result[f'recall_v_le_{limit}'] = float(match.sum()/max(len(ref), 1))
    # Measurement diagnostics use references; matching is only an evaluation step.
    goodfit = np.isfinite(fitted_flux) & (fitted_flux > 0)
    match, ri = old.one_to_one(fit_global[goodfit], quality_xy)
    kept = np.where(goodfit)[0][match]; refs = ri[match]
    train = partition[refs] != 2; test = partition[refs] == 2
    if train.sum() >= 20 and test.sum() >= 20:
        coeff = old.fit_affine(fit_global[kept][train], quality_xy[refs][train])
        pred = old.apply_affine(fit_global[kept][test], coeff)
        radial = np.sqrt(np.sum((pred-quality_xy[refs][test])**2, axis=1))
        med = np.median(radial); mad = 1.4826*np.median(abs(radial-med))
        use = radial <= med + max(3*mad, .05)
        result['astrometric_rms_mas'] = float(np.sqrt(np.mean(radial[use]**2)/2)*old.PIXEL_SCALE_MAS)
        inst = -2.5*np.log10(fitted_flux[kept][test])
        resid = inst + mag_zero - quality_mag[refs][test]
        rmed = np.median(resid); rm = 1.4826*np.median(abs(resid-rmed))
        use = abs(resid-rmed) <= max(3*rm, .03)
        result['photometric_rms_mag'] = float(np.sqrt(np.mean((resid[use]-np.mean(resid[use]))**2)))
        result['measurement_test_matches'] = int(test.sum())
    return result


def artificial_stars(image, rms, psf, mag_zero, density_tree, cluster):
    """Injection/recovery using ten sparse batches, stratified by magnitude/density."""
    rows=[]
    # Local density = reference neighbours within 10 px, derived once only to
    # stratify field crowding; injected positions are otherwise random.
    for mag in (18.0, 20.0, 22.0):
        flux = 10**(-0.4*(mag-mag_zero))
        # The two fields have different crowding ranges; these narrow bins are
        # populated in both and still isolate an explicitly crowded stratum.
        for band, (lo, hi) in {'low':(0,1), 'mid':(2,2), 'high':(3,10_000)}.items():
            inserted=[]; recovered=0; trials=0
            for batch in range(3):
                positions=[]
                while len(positions) < 20 and trials < 10000:
                    trials += 1
                    x,y=RNG.uniform(15, old.CROP_SIZE-15, 2)
                    dcount=len(density_tree.query_ball_point([x+old.CROP_X0,y+old.CROP_Y0], r=10))
                    if lo <= dcount <= hi and all(np.hypot(x-a,y-b)>12 for a,b in positions):
                        positions.append((x,y))
                if not positions:
                    continue
                pos=np.asarray(positions); injected=image+render_psf(image.shape,psf,pos,np.full(len(pos),flux))
                src=base.detect_sources(injected,rms,fwhm=2.2,threshold_sigma=3.0)
                found=np.empty((0,2)) if len(src)==0 else np.c_[src['xcentroid'],src['ycentroid']]
                if len(found):
                    dist,_=cKDTree(found).query(pos,k=1); recovered += int((dist<=2).sum())
                inserted.extend(positions)
            rows.append({'cluster':cluster,'mag':mag,'density_band':band,'injected':len(inserted),
                         'recovered':recovered,'recovery':recovered/max(len(inserted),1)})
    return rows


def run_cluster(cluster):
    image, cat = old.read_cluster(cluster)
    x,y,measured,quality,_ = old.catalog_subsets(cat)
    qxy=np.c_[x[quality],y[quality]]; qmag=np.asarray(cat['Vvega'],float)[quality]
    part=old.ref_cells(qxy)
    sub,rms=base.estimate_background(image)
    src=base.detect_sources(sub,rms,fwhm=2.2,threshold_sigma=3.0)
    initial=np.c_[src['xcentroid'],src['ycentroid']]
    t=time.perf_counter(); psf,nstamp=build_epsf(sub,src)
    detections, oldfit, oldflux, nextra=residual_candidates(sub,rms,psf,initial)
    fitted, flux=fit_catalogue(sub,psf,detections)
    # Catalogue is used only to state F606W magnitude residuals.  The robust
    # zero point is fit on non-test matches, then held fixed for test residuals.
    fitglobal=fitted+np.array([old.CROP_X0,old.CROP_Y0]); match,ri=old.one_to_one(fitglobal,qxy)
    train=(part[ri[match]]!=2)&np.isfinite(flux[match])&(flux[match]>0)
    inst=-2.5*np.log10(flux[match][train]); zp=float(np.median(qmag[ri[match][train]]-inst))
    result=evaluate(cluster,detections,fitted,flux,qxy,qmag,part,zp)
    result.update({'psf_stamps':nstamp,'residual_candidates_added':nextra,'epsf_deblend_s':time.perf_counter()-t,'mag_zero_point':zp})
    injections=artificial_stars(sub,rms,psf,zp,cKDTree(qxy),cluster)
    np.save(OUT/f'{cluster}_epsf.npy',psf)
    return result,injections,psf,image,detections,qxy,part


def main():
    OUT.mkdir(exist_ok=True)
    results=[]; injections=[]; plots=[]
    for cluster in old.CLUSTERS:
        print('ePSF/deblend:',cluster)
        r,inj,psf,image,det,qxy,part=run_cluster(cluster)
        print(json.dumps(r,indent=2)); results.append(r); injections.extend(inj); plots.append((cluster,image,det,qxy,part,psf))
    payload={'protocol':{'empirical_psf':'median of isolated image-only DAO detections; 13x13 stamps; cubic subpixel alignment',
                         'deblend':'one residual DAO pass after simultaneous local neighbour-aware PSF fitting',
                         'artificial_stars':'3 sparse batches x 20 input stars per magnitude/density stratum; density bins are 0-1, 2, and >=3 references within 10 px; 2-pixel recovery radius'},
             'results':results,'artificial_star_recovery':injections}
    (OUT/'hst_epsf_deblend_summary.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    with (OUT/'artificial_star_recovery.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=injections[0].keys());w.writeheader();w.writerows(injections)
    fig,axs=plt.subplots(2,2,figsize=(10,9),constrained_layout=True)
    for ax,(cluster,img,det,qxy,part,psf) in zip(axs[:,0],plots):
        lo,hi=np.percentile(img,[5,99.7]);ax.imshow(img,origin='lower',cmap='gray',vmin=lo,vmax=hi)
        ax.scatter(*(qxy[part==2]-[old.CROP_X0,old.CROP_Y0]).T,s=3,facecolors='none',edgecolors='#31c9ff',linewidth=.25)
        ax.scatter(det[:,0],det[:,1],s=1,c='#ffbd2e',alpha=.45);ax.set_title(cluster.upper()+' ePSF + residual proposals')
    for ax,(_,_,_,_,_,psf) in zip(axs[:,1],plots): ax.imshow(psf,origin='lower',cmap='magma');ax.set_title('empirical PSF');ax.set_xlabel('pixel')
    fig.savefig(OUT/'hst_epsf_deblend.png',dpi=200);plt.close(fig)
    lines=['# HST ePSF, residual-deblend, and artificial-star extension','',
           'This is a separate extension of the v7 fixed benchmark.  It does not alter its zero-shot or target-adaptation result. ePSF stamps are selected from the image alone; official ACS catalogue data are used only for final metrics and a non-test photometric zero point.','',
           '| Cluster | Proposals | Added residual candidates | Test recall | Recall V<=20 | Position RMS / mas | PSF mag RMS / mag |', '|---|---:|---:|---:|---:|---:|---:|']
    for r in results: lines.append(f"| {r['cluster']} | {r['proposals']} | {r['residual_candidates_added']} | {r['test_recall']:.3f} | {r['recall_v_le_20']:.3f} | {r.get('astrometric_rms_mas',float('nan')):.2f} | {r.get('photometric_rms_mag',float('nan')):.3f} |")
    lines += ['', '## Artificial-star recovery', '', '| Cluster | V | Local-density stratum (references within 10 px) | Input | Recovered | Recovery |', '|---|---:|---|---:|---:|---:|']
    for r in injections: lines.append(f"| {r['cluster']} | {r['mag']:.0f} | {r['density_band']} | {r['injected']} | {r['recovered']} | {r['recovery']:.3f} |")
    lines += ['', 'Recovery measures only whether a proposal lies within 2 pixels of the injected location. It is not a claim of catalogue completeness: real unresolved sources and injected-star blends remain part of the deliberately realistic background.']
    (OUT/'hst_epsf_deblend_report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(OUT)


if __name__ == '__main__': main()
