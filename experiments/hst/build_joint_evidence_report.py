#!/usr/bin/env python
"""Build a three-tier, non-pooled AstroCFR evidence report."""
from __future__ import annotations
import csv,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'results/joint_csst_hst_m31_evidence'
HST=ROOT.parent/'CSST_上海电机学院/代码及中间过程文件/fanhuaxing/hst_unified_baseline_results_v5/hst_unified_baseline_summary.json'
M31=ROOT/'results/non_globular_runs/m31_b21_f15/matched_coordinate_scene/paired_common_summary.json'
M31_INDEPENDENT=ROOT/'results/non_globular_runs/m31_b21_f15/matched_coordinate_scene/independent_psf_validation/independent_psf_validation.json'
CSST_CHIPS=(12,13,17,18)
CSST_REC=(.969,.931,.964,.918)
CSST_POS=(17.2,8.2,9.1,8.8)
CSST_MAG=(.0596,.0575,.0802,.0684)

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    h=json.loads(HST.read_text(encoding='utf-8'));m=json.loads(M31.read_text(encoding='utf-8'))
    independent=json.loads(M31_INDEPENDENT.read_text(encoding='utf-8'))
    csst=[{'evidence_tier':'CSST-like registered supplied-catalogue evaluation','field':f'chip{c}','method':'AstroCFR registered branch','reference_recovery':r,'position_rms_mas':p,'magnitude_rms_mag':g,'truth_scope':'supplied top-1000 catalogue; not certified exhaustive scene truth'} for c,r,p,g in zip(CSST_CHIPS,CSST_REC,CSST_POS,CSST_MAG)]
    keep={'dao','sep','photutils_psf','wpdc_rf','wpdc_epsf_deblend','wpdc_spatial_epsf_joint'}
    real=[]
    for r in h['results']:
      if r['method'] not in keep:continue
      real.append({'evidence_tier':'single-stacked HST/ACS external-catalogue evaluation','field':r['cluster'],'method':r['label'],'test_references':r['test_references'],'test_completeness':r['test_completeness'],'dense_v20_recall':r['high_density_v20_recall'],'position_rms_mas':r['astrometric_rms_mas'],'magnitude_rms_mag':r['photometric_rms_mag'],'runtime_s_per_mpix':r['runtime_s_per_mpix'],'truth_scope':'finite official ACSGGCT catalogue; catalogue-match/recovery, not blind scene purity'})
    single=m['single_drz'];dolphot=m['dolphot_three_flc']
    independent_rows=[r for r in independent['recovery'] if r['injection_psf']=='anderson']
    independent_tests=[r for r in independent['between_method_paired_tests'] if r['injection_psf']=='anderson']
    payload={'rules':['Do not pool stars or average percentages across tiers.','CSST supplied-catalogue metrics are not blind purity because the catalogue is not certified exhaustive.','The three globular-cluster rows use one stacked image per field and an external finite catalogue.','M31 single-DRZ results use a paired 174-star common denominator with pre-existing detections excluded.','The official Anderson F475W injection is primary for M31 candidate-recovery interpretation; the image-derived empirical-PSF injection is a controlled model-matched sensitivity result.','M31 DOLPHOT uses native FakeStars on three registered 370-s F475W FLC exposures; it is a separate image domain and is not in the paired single-DRZ ranking.'],'sources':{'hst_unified':str(HST),'m31_paired_empirical':str(M31),'m31_independent_psf':str(M31_INDEPENDENT),'csst_per_chip':'tools/build_manuscript_v42_closed_book_scope.py draw_registered_audit arrays'},'csst_registered':csst,'hst_single_stack':real,'m31_single_drz_independent_anderson':independent_rows,'m31_single_drz_independent_tests':independent_tests,'m31_single_drz_empirical_sensitivity':single,'m31_three_flc_dolphot':dolphot}
    (OUT/'joint_evidence.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    for name,rows in [('csst_registered.csv',csst),('hst_single_stack.csv',real),('m31_single_drz_independent_anderson.csv',independent_rows),('m31_single_drz_independent_tests.csv',independent_tests),('m31_single_drz_empirical_sensitivity.csv',single),('m31_three_flc_dolphot.csv',dolphot)]:
      with (OUT/name).open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
    lines=['# AstroCFR joint evidence report','','This report deliberately separates incompatible truth and image domains. No eight-field grand mean is computed.','','## Tier 1: four CSST-like registered chips','','|Chip|Reference recovery|Position RMS (mas)|Magnitude RMS (mag)|','|---|---:|---:|---:|']
    for r in csst:lines.append(f"|{r['field']}|{r['reference_recovery']:.3f}|{r['position_rms_mas']:.1f}|{r['magnitude_rms_mag']:.3f}|")
    lines+=['','These are supplied-catalogue metrics. The top-1000 catalogue is not certified as exhaustive scene truth; reference-aware precision must not be called blind purity.','','## Tier 2: three single-stacked HST/ACS globular-cluster fields','','|Field|Method|Completeness|Dense V<=20 recovery|Position RMS (mas)|Magnitude RMS (mag)|s/MPix|','|---|---|---:|---:|---:|---:|---:|']
    for r in real:lines.append(f"|{r['field']}|{r['method']}|{r['test_completeness']:.3f}|{r['dense_v20_recall']:.3f}|{r['position_rms_mas']:.2f}|{r['magnitude_rms_mag']:.3f}|{r['runtime_s_per_mpix']:.2f}|")
    lines+=['','## Tier 3a: M31 PHAT single-DRZ independent Anderson-PSF injections','','The same 174 positions are baseline-clear for all four single-image methods. Trials are one-star injections with a 2-pixel recovery radius. The official Anderson F475W ePSF is independent of AstroCFR\'s image-derived recovery ePSF and is projected through three accepted FLC WCS solutions. The renderer does not reproduce the full AstroDrizzle kernel or correlated noise.','','|Density|F475W|Method|n|Recovered|Recovery [95% CI]|Nearest-detection RMS (px)|Median trial time (s)|','|---|---:|---|---:|---:|---|---:|---:|']
    for r in independent_rows:lines.append(f"|{r['density_band']}|{r['input_vegamag_f475w']:.1f}|{r['method']}|{r['common_eligible']}|{r['recovered']}|{r['recovery']:.3f} [{r['ci95_low_wilson']:.3f}, {r['ci95_high_wilson']:.3f}]|{r['nearest_detection_radial_rms_px']:.3f}|{r['runtime_per_trial_median_s']:.3f}|")
    lines+=['','The earlier image-derived empirical-PSF injection is retained in `m31_single_drz_empirical_sensitivity.csv` as a model-matched sensitivity analysis, not the primary independent validation.']
    lines+=['','## Tier 3b: M31 PHAT DOLPHOT three-FLC native FakeStars','','|Density|F475W|n|Recovered|Recovery [95% CI]|Position RMS (px)|Magnitude bias|Magnitude RMS|','|---|---:|---:|---:|---|---:|---:|---:|']
    for r in dolphot:lines.append(f"|{r['density_band']}|{r['input_vegamag_f475w']:.1f}|{r['injected']}|{r['recovered']}|{r['recovery']:.3f} [{r['ci95_low']:.3f}, {r['ci95_high']:.3f}]|{r['position_radial_rms_px_unclipped']:.3f}|{r['magnitude_bias_median']:.3f}|{r['magnitude_rms_about_zero_unclipped']:.3f}|")
    lines+=['','## Defensible interpretation','','Under the independent Anderson injection, AstroCFR ePSF recovers 40/41 low-density and 37/42 high-density F475W=26.5 trials, compared with 33/41 and 28/42 for DAO/Photutils. Exact paired McNemar p-values are 0.0156 and 0.0039, and paired recovery differences are +17.1 and +21.4 percentage points. This candidate-recovery gain costs roughly 0.34 s per local trial and does not establish superior final photometry. DOLPHOT reaches 88.5-90.5% faint-star recovery in the separate three-FLC domain and provides calibrated magnitude residuals; it remains the physical multi-exposure backend result, not a directly ranked single-DRZ competitor.']
    (OUT/'JOINT_EVIDENCE_REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(OUT/'JOINT_EVIDENCE_REPORT.md')
if __name__=='__main__':main()
