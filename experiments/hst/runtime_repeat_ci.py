#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Repeated CPU runtime/RSS measurements for the controlled HST methods."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import hst_acsggct_benchmark as old
import real_data_zero_shot_generalization as base
import real_data_domain_adaptation as adapt
import hst_unified_baseline_benchmark as b

HERE=Path(__file__).resolve().parent
OUT=HERE/'hst_unified_baseline_results_v3'

def main():
    rows=[]
    for cluster in old.CLUSTERS:
        image,_=old.read_cluster(cluster);sub,rms=base.estimate_background(image)
        pre=base.detect_sources(sub,rms,fwhm=2.0,threshold_sigma=10);mod=adapt.load_pipeline();fwhm=float(np.clip(mod.estimate_psf_fwhm(sub,pre,rms,min_snr=20,max_sources=40),1.5,4.0))
        rfctx=b.prepare_wpdc_rf(cluster,sub,rms,fwhm)
        for method in b.METHODS:
            # One warm-up run removes import/JIT effects from repeated timing.
            b.measure(lambda:b.method_run(method,sub,rms,fwhm,rfctx))
            times=[];mem=[]
            for _ in range(5):
                (_,elapsed,delta)=b.measure(lambda:b.method_run(method,sub,rms,fwhm,rfctx));times.append(elapsed/(old.CROP_SIZE**2)*1e6);mem.append(delta)
            rows.append({'cluster':cluster,'method':method,'n_repeats':5,'runtime_s_per_mpix_median':float(np.median(times)),'runtime_s_per_mpix_ci95_percentile':[float(np.percentile(times,2.5)),float(np.percentile(times,97.5))],'rss_delta_mb_median':float(np.median(mem)),'rss_delta_mb_range':[float(min(mem)),float(max(mem))],'raw_runtime_s_per_mpix':times})
    (OUT/'runtime_repeat_ci.json').write_text(json.dumps(rows,indent=2),encoding='utf-8');print(OUT/'runtime_repeat_ci.json')
if __name__=='__main__':main()
