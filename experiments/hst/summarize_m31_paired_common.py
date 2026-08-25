#!/usr/bin/env python
"""Create the paired common-denominator M31 artificial-star audit."""
from __future__ import annotations
import csv,json,math
from pathlib import Path
import numpy as np
from scipy.stats import binomtest
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'results/non_globular_runs/m31_b21_f15'
SINGLE=BASE/'matched_coordinate_scene/strict_single_injection'
METHODS=('dao','sep','photutils','astrocfr_epsf')

def wilson(k,n,z=1.96):
    p=k/n;d=1+z*z/n;m=(p+z*z/(2*n))/d;h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [max(0,m-h),min(1,m+h)]
def paired_p(a,b):
    b_only=sum((not x) and y for x,y in zip(a,b));a_only=sum(x and (not y) for x,y in zip(a,b));n=a_only+b_only
    return {'a_only':a_only,'b_only':b_only,'mcnemar_exact_p':float(binomtest(min(a_only,b_only),n,.5).pvalue) if n else 1.0}
def main():
    raw={m:json.loads((SINGLE/f'{m}_summary.json').read_text()) for m in METHODS}
    by={m:{str(r['fake_id']):r for r in d['rows'] if r.get('status')=='eligible'} for m,d in raw.items()}
    ids=sorted(set.intersection(*[set(v) for v in by.values()]),key=int)
    rows=[]
    for density in ('low','high'):
      for mag in (24.5,26.5):
        use=[i for i in ids if by['photutils'][i]['density_band']==density and float(by['photutils'][i]['input_vegamag_f475w'])==mag]
        for method in METHODS:
          q=[by[method][i] for i in use];k=sum(bool(r['recovered']) for r in q);dist=np.array([float(r['injection_nearest_px']) for r in q if r['recovered']])
          rows.append({'image_domain':'single PHAT F475W DRZ','method':method,'density_band':density,'input_vegamag_f475w':mag,'common_eligible':len(q),'recovered':k,'recovery':k/len(q),'ci95_low':wilson(k,len(q))[0],'ci95_high':wilson(k,len(q))[1],'position_radial_rms_px':float(np.sqrt(np.mean(dist**2))) if len(dist) else None,'runtime_per_trial_median_s':float(np.median([r['runtime_s'] for r in q]))})
    tests=[]
    for density in ('low','high'):
      for mag in (24.5,26.5):
        use=[i for i in ids if by['photutils'][i]['density_band']==density and float(by['photutils'][i]['input_vegamag_f475w'])==mag]
        a=[bool(by['astrocfr_epsf'][i]['recovered']) for i in use]
        for other in ('dao','sep','photutils'):
          b=[bool(by[other][i]['recovered']) for i in use]
          tests.append({'density_band':density,'input_vegamag_f475w':mag,'method_a':'astrocfr_epsf','method_b':other,'n':len(use),**paired_p(a,b)})
    # DOLPHOT is aggregated separately because its native FakeStars trials are
    # in the three-FLC domain and are not the same pixel-level injection.
    d=json.loads((BASE/'dolphot_fake_n800_summary.json').read_text())
    dagg=[]
    for density in ('low','high'):
      for band,mag in (('bright',24.5),('faint',26.5)):
        q=[r for r in d['aggregate'] if r['density_band']==density and r['magnitude_band']==band]
        n=sum(int(r['injected']) for r in q);k=sum(int(r['recovered']) for r in q)
        # Pooled measurement RMS is recomputed from recovered native rows.
        rr=[r for r in d['rows'] if r['density_band']==density and r['magnitude_band']==band and r['recovered']]
        sep=np.array([float(r['separation_px']) for r in rr]);dm=np.array([float(r['mag_error']) for r in rr])
        dagg.append({'image_domain':'three registered 370-s F475W FLC exposures','method':'DOLPHOT native FakeStars','density_band':density,'input_vegamag_f475w':mag,'injected':n,'recovered':k,'recovery':k/n,'ci95_low':wilson(k,n)[0],'ci95_high':wilson(k,n)[1],'position_radial_rms_px_unclipped':float(np.sqrt(np.mean(sep**2))),'magnitude_bias_median':float(np.median(dm)),'magnitude_rms_about_zero_unclipped':float(np.sqrt(np.mean(dm**2)))})
    out=BASE/'matched_coordinate_scene';payload={'scope':'Single-DRZ methods use the identical 174 baseline-clear common denominator. DOLPHOT remains a separate three-FLC image-domain result and is not included in paired tests.','common_ids':ids,'single_drz':rows,'paired_tests':tests,'dolphot_three_flc':dagg}
    (out/'paired_common_summary.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    for name,data in [('paired_common_single_drz.csv',rows),('paired_mcnemar_tests.csv',tests),('dolphot_three_flc_stratified.csv',dagg)]:
      with (out/name).open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=data[0].keys());w.writeheader();w.writerows(data)
    lines=['# M31 B21-F15 common-coordinate artificial-star audit','',payload['scope'],'','## Single PHAT DRZ: paired common denominator','','|Density|F475W|Method|n|Recovered|Recovery [95% CI]|Position radial RMS (px)|Median trial time (s)|','|---|---:|---|---:|---:|---|---:|---:|']
    for r in rows:lines.append(f"|{r['density_band']}|{r['input_vegamag_f475w']:.1f}|{r['method']}|{r['common_eligible']}|{r['recovered']}|{r['recovery']:.3f} [{r['ci95_low']:.3f}, {r['ci95_high']:.3f}]|{r['position_radial_rms_px']:.3f}|{r['runtime_per_trial_median_s']:.3f}|")
    lines+=['','## Three-FLC DOLPHOT native FakeStars (separate image domain)','','|Density|F475W|n|Recovered|Recovery [95% CI]|Position RMS (px)|Magnitude bias|Magnitude RMS|','|---|---:|---:|---:|---|---:|---:|---:|']
    for r in dagg:lines.append(f"|{r['density_band']}|{r['input_vegamag_f475w']:.1f}|{r['injected']}|{r['recovered']}|{r['recovery']:.3f} [{r['ci95_low']:.3f}, {r['ci95_high']:.3f}]|{r['position_radial_rms_px_unclipped']:.3f}|{r['magnitude_bias_median']:.3f}|{r['magnitude_rms_about_zero_unclipped']:.3f}|")
    lines+=['','Important: common sky coordinates do not make the FLC and DRZ injections pixel-identical. Do not rank DOLPHOT and the single-stack methods in one undifferentiated leaderboard.']
    (out/'PAIRED_COMMON_RESULTS.md').write_text('\n'.join(lines),encoding='utf-8')
    # Compact paper candidate: recovery with Wilson intervals and per-trial
    # runtime.  DOLPHOT is intentionally omitted because it is a different
    # three-FLC image domain.
    labels={'dao':'DAO','sep':'SEP','photutils':'Photutils','astrocfr_epsf':'AstroCFR ePSF'}
    colors={'dao':'#4C78A8','sep':'#9C755F','photutils':'#59A14F','astrocfr_epsf':'#E15759'}
    fig,axes=plt.subplots(1,2,figsize=(9.4,3.8),constrained_layout=True)
    x=np.arange(4);width=.19
    strata=[('low',24.5),('low',26.5),('high',24.5),('high',26.5)]
    xt=[f'{d}\nF475W={m:.1f}' for d,m in strata]
    for j,method in enumerate(METHODS):
        q=[next(r for r in rows if r['method']==method and r['density_band']==d and r['input_vegamag_f475w']==m) for d,m in strata]
        y=np.array([r['recovery'] for r in q]);lo=np.maximum(0,y-np.array([r['ci95_low'] for r in q]));hi=np.maximum(0,np.array([r['ci95_high'] for r in q])-y)
        axes[0].bar(x+(j-1.5)*width,y,width,label=labels[method],color=colors[method],yerr=np.vstack([lo,hi]),capsize=2,linewidth=.5)
    axes[0].set_xticks(x,xt);axes[0].set_ylim(0,1.08);axes[0].set_ylabel('Recovery fraction');axes[0].grid(axis='y',alpha=.22)
    axes[0].legend(frameon=False,ncol=2,fontsize=8,loc='lower left')
    med=[]
    for method in METHODS:
        q=[r['runtime_per_trial_median_s'] for r in rows if r['method']==method]
        med.append(float(np.median(q)))
    axes[1].bar(np.arange(4),med,color=[colors[m] for m in METHODS]);axes[1].set_xticks(np.arange(4),[labels[m] for m in METHODS],rotation=18,ha='right');axes[1].set_yscale('log');axes[1].set_ylabel('Median local-trial time (s, log scale)');axes[1].grid(axis='y',alpha=.22)
    axes[0].set_title('(a) Paired common-denominator recovery');axes[1].set_title('(b) Measured local cost')
    fig.savefig(out/'m31_paired_recovery_cost.png',dpi=300,bbox_inches='tight');plt.close(fig)
    print(json.dumps({'common_n':len(ids),'single_drz':rows,'paired_tests':tests,'dolphot':dagg},indent=2))
if __name__=='__main__':main()
