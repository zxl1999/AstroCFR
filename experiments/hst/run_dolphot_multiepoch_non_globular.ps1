param(
    [Parameter(Mandatory=$true)][ValidateSet('m81_deep','ngc2976_deep','m31_b21_f15')][string]$Field,
    [string]$DataRoot,
    [string]$WorkRoot,
    [string]$RunName = 'alignonly_registered',
    [int]$MaxExposures = 0,
    [string[]]$IncludeFiles = @(),
    [switch]$AlignOnly,
    [double]$AlignTolerance = 8.0,
    [double]$AlignStepPixels = 1.0,
    [double]$AlignmentSigma = 3.5,
    [ValidateSet(1,2,3)][int]$AlignMode = 3
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$bin = Join-Path $repo 'external\dolphot\dolphot2.1\bin'
$DataRoot = if ($DataRoot) { $DataRoot } else { Join-Path $repo 'external\non_globular_fields' }
$WorkRoot = if ($WorkRoot) { $WorkRoot } else { Join-Path $repo 'results\non_globular_runs' }
$source = Join-Path (Join-Path $DataRoot $Field) 'flc'
$work = Join-Path (Join-Path $WorkRoot $Field) $RunName
New-Item -ItemType Directory -Force -Path $work | Out-Null
if (-not (Test-Path -LiteralPath $source)) { throw "Missing FLC source directory: $source" }
$flc = @(Get-ChildItem -LiteralPath $source -Filter '*_flc.fits' | Sort-Object Name)
if ($IncludeFiles.Count -gt 0) {
  $wanted = @{}; foreach ($name in $IncludeFiles) { $wanted[$name] = $true }
  $flc = @($flc | Where-Object { $wanted.ContainsKey($_.Name) })
  if ($flc.Count -ne $IncludeFiles.Count) { throw "At least one IncludeFiles entry was not found in $source" }
}
if ($flc.Count -lt 2) { throw "Need at least two FLC exposures; found $($flc.Count)" }
if ($MaxExposures -gt 0 -and $flc.Count -gt $MaxExposures) { $flc = @($flc | Select-Object -First $MaxExposures) }
Push-Location $work
try {
  foreach ($file in $flc) {
    $dest = Join-Path $work $file.Name
    if (-not (Test-Path -LiteralPath $dest)) { Copy-Item -LiteralPath $file.FullName -Destination $dest }
    $base = [IO.Path]::GetFileNameWithoutExtension($file.Name)
    if (-not (Test-Path -LiteralPath (Join-Path $work ($base+'.sky.fits')))) {
      & (Join-Path $bin 'acsmask.exe') '-keepcr' $file.Name
      if ($LASTEXITCODE -ne 0) { throw "acsmask failed for $($file.Name)" }
      & (Join-Path $bin 'calcsky.exe') $base 15 35 -128 2.25 2.00
      if ($LASTEXITCODE -ne 0) { throw "calcsky failed for $base" }
    }
  }
  $lines = @(
    "Nimg = $($flc.Count)", 'img_shift = 0 0', 'img_xform = 1 0 0',
    'img_RAper = 3', 'img_RChi = 2', 'img_RSky = 4 10', 'img_RPSF = 15', 'RCombine = 1.5',
    'SigFind = 2.5', 'SigFinal = 3.5', 'SigPSF = 5.0', 'PSFPhot = 1', 'PSFPhotIt = 2',
    'FitSky = 2', 'SkipSky = 1', 'Force1 = 0', 'UseWCS = 0', "Align = $AlignMode",
    'AlignIter = 5', "AlignTol = $AlignTolerance", "AlignStep = $AlignStepPixels", "SigAlign = $AlignmentSigma",
    "AlignOnly = $(if ($AlignOnly) { 1 } else { 0 })", 'Rotate = 1', 'SecondPass = 1', 'PSFres = 1',
    'PSFresSpatial = 1', 'PSFresSuper = 3', 'ACSuseCTE = 1', 'ACSpsfType = 1', 'InterpPSFlib = 1',
    'FlagMask = 4', 'ApCor = 1', 'VerboseData = 1'
  )
  for ($i=0; $i -lt $flc.Count; $i++) {
    $base=[IO.Path]::GetFileNameWithoutExtension($flc[$i].Name); $lines += "img$($i+1)_file = $base"
  }
  $shiftScript = Join-Path $repo 'experiments\hst\compute_flc_wcs_shifts.py'
  $shiftArgs = @($shiftScript,'--reference',$flc[0].FullName,'--images') + @($flc | ForEach-Object {$_.FullName})
  $shiftJson = (& python @shiftArgs | Out-String | ConvertFrom-Json); $shiftManifest=@{}
  for ($i=0; $i -lt $flc.Count; $i++) {
    $base=[IO.Path]::GetFileNameWithoutExtension($flc[$i].Name); $shift=$shiftJson.$base
    $lines += "img$($i+1)_shift = $($shift.x) $($shift.y)"; $shiftManifest[$base]=$shift
  }
  Set-Content -LiteralPath 'joint.param' -Value $lines -Encoding ascii
  @{field=$Field;run_name=$RunName;exposure_count=$flc.Count;align_only=[bool]$AlignOnly;selected=@($flc.Name);wcs_shifts=$shiftManifest;created_utc=(Get-Date).ToUniversalTime().ToString('o')} |
    ConvertTo-Json -Depth 6 | Set-Content -LiteralPath 'exposure_manifest.json' -Encoding utf8
  & (Join-Path $bin 'dolphot.exe') 'joint' '-pjoint.param' *> 'dolphot.stdout.log'
  if ($LASTEXITCODE -ne 0) { throw "DOLPHOT failed for $Field" }
  $warnings=Join-Path $work 'joint.warnings'
  if (Test-Path $warnings) { Get-Content $warnings | Select-String 'No alignment stars matched|Large alignment scatter|Only 0 stars for PSF measurement|Only 0 aperture stars' | Set-Content 'acceptance_gate_matches.txt' }
  Write-Output "Completed $Field at $work"
} finally { Pop-Location }
