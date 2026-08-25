param(
 [Parameter(Mandatory=$true)][string]$FakeList,
 [string]$RunDir = (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'results\non_globular_runs\m31_b21_f15\joint_3x370s_f475w'),
 [string]$OutputName = 'joint.fake'
)
$ErrorActionPreference='Stop'
$repo=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$bin=Join-Path $repo 'external\dolphot\dolphot2.1\bin\dolphot.exe'
$fake=(Resolve-Path $FakeList).Path
Push-Location $RunDir
try {
  $start=Get-Date
  & $bin 'joint' '-pjoint.param' "FakeStars=$fake" "FakeOut=$OutputName" 'FakeMatch=2.0' 'FakePSF=2.0' 'FakeStarPSF=1' 'RandomFake=1' 'FakePad=15' *> "$OutputName.stdout.log"
  if($LASTEXITCODE -ne 0){throw "DOLPHOT fake-star run failed"}
  $elapsed=((Get-Date)-$start).TotalSeconds
  Set-Content "$OutputName.walltime_seconds.txt" ([string]::Format([Globalization.CultureInfo]::InvariantCulture,'{0:F3}',$elapsed)) -Encoding ascii
} finally {Pop-Location}
