#!/usr/bin/env python
"""Summarize native DOLPHOT M31 artificial-star recovery by fixed stratum."""
from __future__ import annotations
import argparse,csv,json
from pathlib import Path
import numpy as np
from scipy.stats import beta

def wilson(k,n,z=1.96):
 p=k/n; d=1+z*z/n; m=(p+z*z/(2*n))/d; h=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
 return [float(max(0,m-h)),float(min(1,m+h))]
def rr(v):
 v=np.asarray(v,float); med=np.median(v); mad=1.4826*np.median(abs(v-med)); g=abs(v-med)<=max(3*mad,1e-6)
 return float(np.sqrt(np.mean((v[g]-np.mean(v[g]))**2))),int(g.sum()),float(np.median(v))
def main():
 p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--scene',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 raw=np.loadtxt(a.input); scene=list(csv.DictReader(a.scene.open()))
 if len(raw)!=len(scene):raise RuntimeError(f'fake output {len(raw)} != scene {len(scene)}')
 rows=[]
 for rec,v in zip(scene,raw):
  # Fake output indices: injected ext/chip/x/y, image1 counts/mag ...; fitted
  # reference x/y at 12/13, SNR 15, type 20; combined F475W VEGAMAG at 26.
  dx=(v[12]-v[2]);dy=(v[13]-v[3]); sep=float(np.hypot(dx,dy)); recovered=bool(np.isfinite(v[12]) and np.isfinite(v[13]) and v[20] in (1,2) and sep<=2)
  # Older manifests record the numeric injection magnitude but not its label.
  rec = dict(rec)
  rec.setdefault('magnitude_band', 'bright' if float(rec['input_vegamag_f475w']) <= 24.5 else 'faint')
  rows.append({**rec,'recovered':recovered,'separation_px':sep,'position_x_error_px':float(dx),'position_y_error_px':float(dy),'input_mag':float(v[5]),'recovered_mag':float(v[26]),'mag_error':float(v[26]-v[5]),'snr':float(v[15])})
 keys=sorted({(r['extension'],r['density_band'],r['magnitude_band']) for r in rows}); agg=[]
 for key in keys:
  q=[r for r in rows if (r['extension'],r['density_band'],r['magnitude_band'])==key]; good=[r for r in q if r['recovered']]; n=len(q);k=len(good)
  dxy=np.array([[r['position_x_error_px'],r['position_y_error_px']] for r in good]); dm=np.array([r['mag_error'] for r in good])
  pos_rms,pi,_=rr(np.hypot(dxy[:,0],dxy[:,1])) if len(good)>=5 else (None,0,None); mag_rms,mi,mb=rr(dm) if len(good)>=5 else (None,0,None)
  agg.append({'extension':key[0],'density_band':key[1],'magnitude_band':key[2],'injected':n,'recovered':k,'recovery':k/n,'recovery_ci95_wilson':wilson(k,n),'position_radial_rms_px':pos_rms,'position_inliers':pi,'mag_bias_median':mb,'mag_rms':mag_rms,'photometric_inliers':mi})
 a.out.parent.mkdir(parents=True,exist_ok=True);a.out.with_suffix('.json').write_text(json.dumps({'protocol':'DOLPHOT native FakeStars; same three 370-s F475W FLCs, source-registration accepted; recovery=type 1/2 within 2 px','aggregate':agg,'rows':rows},indent=2),encoding='utf-8')
 with a.out.with_suffix('.csv').open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=agg[0].keys());w.writeheader();w.writerows(agg)
 print(json.dumps(agg,indent=2))
if __name__=='__main__':main()
