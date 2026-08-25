#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Reader/interpolator for Anderson ACS/WFC spatially varying standard ePSFs.

The public `STDPSF_ACSWFC_F606W.fits` table contains 9 x 10 detector PSFs,
each sampled four times per native ACS pixel.  This module follows the published
grid interpolation convention used by HST1PASS, but keeps the implementation
in Python so it can be audited alongside WPDC.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from astropy.io import fits
from scipy.ndimage import map_coordinates


class ACSWFCStandardPSF:
    def __init__(self, path: str | Path):
        with fits.open(path) as h:
            self.data=np.asarray(h[0].data,float)
            hdr=h[0].header
        self.nx=int(hdr['NXPSFS']);self.ny=int(hdr['NYPSFS'])
        self.xgrid=np.array([hdr[f'IPSFX{i:02d}'] for i in range(1,self.nx+1)],float)
        self.ygrid=np.array([hdr[f'JPSFY{i:02d}'] for i in range(1,self.ny+1)],float)
        self.grid=self.data.reshape(self.ny,self.nx,101,101)

    @staticmethod
    def physical_y(y, ccdchip):
        # ACS/WFC STDPSF y-grid is continuous across the two 2048-pixel chips.
        # HST SCI extension 1 has CCDCHIP=2 and occupies the lower 0--2048
        # standard-PSF interval; CCDCHIP=1 occupies the upper interval.  This
        # follows HST1PASS/DOLPHOT's F475W grid convention.
        return float(y)+(2048.0 if int(ccdchip)==1 else 0.0)

    @staticmethod
    def _bracket(grid, value):
        # The y grid deliberately repeats 2048 at the chip boundary.
        upper=int(np.searchsorted(grid,value,side='right'))
        upper=int(np.clip(upper,1,len(grid)-1)); lower=upper-1
        span=grid[upper]-grid[lower]
        frac=0.0 if span<=0 else float(np.clip((value-grid[lower])/span,0,1))
        return lower,upper,frac

    def local_psf(self, x, y, ccdchip):
        ix0,ix1,fx=self._bracket(self.xgrid,float(x))
        iy0,iy1,fy=self._bracket(self.ygrid,self.physical_y(y,ccdchip))
        a=self.grid[iy0,ix0];b=self.grid[iy0,ix1];c=self.grid[iy1,ix0];d=self.grid[iy1,ix1]
        psf=(1-fx)*(1-fy)*a+fx*(1-fy)*b+(1-fx)*fy*c+fx*fy*d
        return np.maximum(psf,0.0)

    def values(self, x, y, ccdchip, xx, yy):
        """Pixel-integrated ePSF values at native detector pixel centres."""
        psf=self.local_psf(x,y,ccdchip)
        # Native pixel centres are separated by four standard-ePSF samples.
        return map_coordinates(psf,[50.0+4.0*(yy-y),50.0+4.0*(xx-x)],order=3,mode='constant',cval=0.0)
