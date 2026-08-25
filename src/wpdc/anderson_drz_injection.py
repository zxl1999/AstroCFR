#!/usr/bin/env python
"""Render an independent Anderson F475W standard ePSF on a PHAT DRZ patch.

For every DRZ output-pixel centre, the renderer transforms the sky coordinate
back to each accepted FLC exposure and evaluates the official spatially
varying ACS/WFC standard ePSF there.  The three exposure contributions are
averaged and normalized.  This approximates the local dithered DRZ PSF but
does not reproduce the full AstroDrizzle kernel or correlated-noise process.
"""
from __future__ import annotations
from pathlib import Path
import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from scipy.ndimage import gaussian_filter

from acs_stdpsf import ACSWFCStandardPSF


class AndersonDRZRenderer:
    def __init__(self, standard_psf: str|Path, drz: str|Path, flcs: list[str|Path], drz_blur_sigma_px: float=0.0):
        self.standard=ACSWFCStandardPSF(standard_psf)
        self.drz_blur_sigma_px=float(drz_blur_sigma_px)
        self._handles=[]
        hd=fits.open(drz,memmap=True);self._handles.append(hd);self.drz_wcs=WCS(hd[1].header,fobj=hd)
        self.flc=[]
        for path in flcs:
            h=fits.open(path,memmap=True);self._handles.append(h)
            self.flc.append({logical:(WCS(h[hdu].header,fobj=h),int(h[hdu].header['CCDCHIP'])) for logical,hdu in ((1,1),(2,4))})

    def close(self):
        for h in self._handles:h.close()
        self._handles=[]

    def unit_patch(self, drz_x:float, drz_y:float, logical_extension:int, half:int=12):
        # FITS WCS calls use origin=1, consistent with the mapped FakeStars
        # coordinates. Array insertion later converts via rounded centres.
        ix,iy=int(round(drz_x)),int(round(drz_y))
        yy,xx=np.mgrid[iy-half:iy+half+1,ix-half:ix+half+1]
        out=np.zeros(xx.shape,float)
        sky_grid=self.drz_wcs.all_pix2world(np.c_[xx.ravel(),yy.ravel()],1)
        sky_star=self.drz_wcs.all_pix2world(np.array([[drz_x,drz_y]]),1)
        for mapping in self.flc:
            wcs,chip=mapping[int(logical_extension)]
            grid=wcs.all_world2pix(sky_grid,1);star=wcs.all_world2pix(sky_star,1)[0]
            val=self.standard.values(star[0],star[1],chip,grid[:,0],grid[:,1]).reshape(xx.shape)
            out+=np.maximum(val,0)
        # The WCS projection omits the AstroDrizzle resampling kernel and is
        # consequently sharper than the measured PHAT DRZ stellar core.  A
        # fixed, registered Gaussian output-grid kernel may be supplied to
        # match the image-measured FWHM while retaining the independent
        # Anderson spatial PSF shape.  This is not a full drizzle simulation.
        if self.drz_blur_sigma_px>0:
            out=gaussian_filter(out,self.drz_blur_sigma_px,mode='constant',cval=0.0)
        total=out.sum()
        if not np.isfinite(total) or total<=0:raise RuntimeError(f'Invalid Anderson DRZ PSF sum {total}')
        return out/total

    def add_star(self, image:np.ndarray, local_x:float, local_y:float, drz_x:float, drz_y:float, logical_extension:int, flux:float, half:int=12):
        model=np.array(image,copy=True);psf=self.unit_patch(drz_x,drz_y,logical_extension,half)
        ix,iy=int(round(local_x)),int(round(local_y))
        if iy-half<0 or ix-half<0 or iy+half>=image.shape[0] or ix+half>=image.shape[1]:raise ValueError('Anderson injection exceeds local patch')
        model[iy-half:iy+half+1,ix-half:ix+half+1]+=float(flux)*psf
        return model,psf
