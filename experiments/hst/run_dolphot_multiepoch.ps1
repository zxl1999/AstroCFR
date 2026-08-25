param(
    [Parameter(Mandatory=$true)][ValidateSet('ngc6752','ngc1851')][string]$Cluster,
    [string]$DataRoot,
    [string]$WorkRoot,
    [string]$RunName = 'deep_registered',
    [double]$MinExposureFraction = 0.5,
    [double]$AlignTolerance = 8.0,
    [double]$AlignStepPixels = 1.0,
    [double]$AlignmentSigma = 3.5,
    [switch]$AlignOnly,
    [ValidateSet(1,2,3)][int]$AlignMode = 3
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$bin = Join-Path $repo 'external\dolphot\dolphot2.1\bin'
$DataRoot = if ($DataRoot) { $DataRoot } else { Join-Path $repo 'external\dolphot\flc_multiepoch' }
$WorkRoot = if ($WorkRoot) { $WorkRoot } else { Join-Path $repo 'external\dolphot\multiepoch_runs' }
$source = Join-Path $DataRoot $Cluster
$work = Join-Path $WorkRoot (Join-Path $Cluster $RunName)
$out = 'joint'

if (-not (Test-Path -LiteralPath $source)) { throw "Missing FLC source directory: $source" }
New-Item -ItemType Directory -Force -Path $work | Out-Null

$flcAll = Get-ChildItem -LiteralPath $source -Filter '*_flc.fits' | Sort-Object Name
$withExposure = foreach ($file in $flcAll) {
    $seconds = [double](& python -c "from astropy.io import fits; import sys; print(fits.getheader(sys.argv[1], 0)['EXPTIME'])" $file.FullName)
    [PSCustomObject]@{ File = $file; Exposure = $seconds }
}
if ($withExposure.Count -eq 0) { throw "No FLC files found in $source" }
$median = ($withExposure | Sort-Object Exposure | Select-Object -ExpandProperty Exposure)
if ($median.Count % 2) { $median = $median[[int]($median.Count / 2)] } else { $median = ($median[$median.Count / 2 - 1] + $median[$median.Count / 2]) / 2 }
$minimum = $median * $MinExposureFraction
$selected = @($withExposure | Where-Object { $_.Exposure -ge $minimum } | Sort-Object @{Expression='Exposure'; Descending=$true}, @{Expression={$_.File.Name}; Descending=$false})
$flc = @($selected | ForEach-Object { $_.File })
if ($flc.Count -lt 4) { throw "Need at least four FLC exposures; found $($flc.Count)" }
if ($flc.Count -ne $flcAll.Count) {
    Write-Warning "Excluded shallow exposures below $minimum s: $((Compare-Object $flcAll.Name $flc.Name | Where-Object SideIndicator -eq '<=' | Select-Object -ExpandProperty InputObject) -join ', ')"
}
Push-Location $work
try {
    foreach ($file in $flc) {
        $destination = Join-Path $work $file.Name
        if (-not (Test-Path -LiteralPath $destination)) { Copy-Item -LiteralPath $file.FullName -Destination $destination }
    }
    # For >=4 dithered ACS exposures, retain repaired cosmic-ray pixels and let
    # DOLPHOT's multi-exposure fitting downweight inconsistent measurements.
    foreach ($file in $flc) {
        $local = Join-Path $work $file.Name
        if (-not (Test-Path -LiteralPath ($local -replace '\.fits$','.sky.fits'))) {
            & (Join-Path $bin 'acsmask.exe') '-keepcr' $file.Name
            if ($LASTEXITCODE -ne 0) { throw "acsmask failed for $($file.Name)" }
            $base = [IO.Path]::GetFileNameWithoutExtension($file.Name)
            & (Join-Path $bin 'calcsky.exe') $base 15 35 -128 2.25 2.00
            if ($LASTEXITCODE -ne 0) { throw "calcsky failed for $base" }
        }
    }
    $lines = @(
        "Nimg = $($flc.Count)",
        'img_shift = 0 0', 'img_xform = 1 0 0',
        'img_RAper = 3', 'img_RChi = 2', 'img_RSky = 4 10', 'img_RPSF = 15', 'RCombine = 1.5',
        'SigFind = 2.5', 'SigFinal = 3.5', 'SigPSF = 5.0',
        'PSFPhot = 1', 'PSFPhotIt = 2', 'FitSky = 2', 'SkipSky = 1',
        "Force1 = 0", 'UseWCS = 0', "Align = $AlignMode", 'AlignIter = 5', "AlignTol = $AlignTolerance", "AlignStep = $AlignStepPixels", "SigAlign = $AlignmentSigma", "AlignOnly = $(if ($AlignOnly) { 1 } else { 0 })", 'Rotate = 1',
        'SecondPass = 1', 'PSFres = 1', 'PSFresSpatial = 1', 'PSFresSuper = 3',
        'ACSuseCTE = 1', 'ACSpsfType = 1', 'InterpPSFlib = 1', 'FlagMask = 4', 'ApCor = 1', 'VerboseData = 1'
    )
    for ($i = 0; $i -lt $flc.Count; $i++) {
        $base = [IO.Path]::GetFileNameWithoutExtension($flc[$i].Name)
        $lines += "img$($i+1)_file = $base"
    }
    $shiftScript = Join-Path $repo 'experiments\hst\compute_flc_wcs_shifts.py'
    $shiftArgs = @($shiftScript, '--reference', $flc[0].FullName, '--images') + @($flc | ForEach-Object { $_.FullName })
    $shiftJson = (& python @shiftArgs | Out-String | ConvertFrom-Json)
    $shiftManifest = @{}
    for ($i = 0; $i -lt $flc.Count; $i++) {
        $base = [IO.Path]::GetFileNameWithoutExtension($flc[$i].Name)
        $shift = $shiftJson.$base
        $lines += "img$($i+1)_shift = $($shift.x) $($shift.y)"
        $shiftManifest[$base] = $shift
    }
    Set-Content -LiteralPath "$out.param" -Value $lines -Encoding ascii
    [PSCustomObject]@{
        cluster = $Cluster; run_name = $RunName; exposure_selection = 'FLC exposures >= MinExposureFraction x median EXPTIME';
        min_exposure_fraction = $MinExposureFraction; median_exposure_s = $median; minimum_exposure_s = $minimum;
        align_tolerance_px = $AlignTolerance; align_step_px = $AlignStepPixels; alignment_sigma = $AlignmentSigma;
        selected = @($selected | ForEach-Object { @{ filename = $_.File.Name; exptime_s = $_.Exposure } });
        wcs_shifts = $shiftManifest
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath 'exposure_manifest.json' -Encoding utf8
    & (Join-Path $bin 'dolphot.exe') $out "-p$out.param"
    if ($LASTEXITCODE -ne 0) { throw "DOLPHOT failed for $Cluster" }
    $warnings = Join-Path $work "$out.warnings"
    if ((Test-Path -LiteralPath $warnings) -and
        (Select-String -LiteralPath $warnings -Pattern 'No alignment stars matched|Large alignment scatter|Only 0 stars for PSF measurement|Only 0 aperture stars' -Quiet)) {
        throw "DOLPHOT completed but failed the registered alignment/PSF acceptance gate. Inspect $warnings; do not evaluate or report this run."
    }
    Write-Output "Completed $Cluster at $work"
} finally { Pop-Location }
