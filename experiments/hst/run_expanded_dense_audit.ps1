param(
    [string]$Root = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string[]]$Clusters = @('ngc2808', 'ngc5286', 'ngc6388', 'ngc6441'),
    [string]$Aria = 'aria2c'
)

$ErrorActionPreference = 'Stop'
$expectedBytes = 144002880
$catalogueBytes = @{
    'ngc2808' = 55263570
    'ngc5286' = 37720854
    'ngc6388' = 62907228
    'ngc6441' = 68842152
    'ngc0104' = 28309929
    'ngc0362' = 22572084
    'ngc6093' = 24497229
    'ngc6624' = 12682692
    'ngc6397' = 2882263
    'ngc6752' = 9454606
    'ngc1851' = 25839908
}
$data = Join-Path $Root 'external\acsggct_expanded'
$results = Join-Path $Root 'results\acsggct_expanded_high_density_precision'
New-Item -ItemType Directory -Force -Path $results | Out-Null

function Assert-ValidFits([string]$ImagePath) {
    $size = (Get-Item -LiteralPath $ImagePath).Length
    if ($size -ne $expectedBytes) { throw "Incomplete image: $ImagePath ($size bytes)" }
    @'
from astropy.io import fits
import sys
with fits.open(sys.argv[1], memmap=False) as h:
    assert h[0].data.shape == (6000, 6000), h[0].data.shape
print('FITS validation passed')
'@ | python - $ImagePath
}

function Download-Asset([string]$Cluster, [string]$Suffix, [Int64]$ExpectedSize) {
    $name = "hlsp_acsggct_hst_acs-wfc_${Cluster}_${Suffix}"
    $path = Join-Path $data $name
    if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path).Length -eq $ExpectedSize)) {
        return $path
    }
    $url = "https://archive.stsci.edu/pub/hlsp/acsggct/${Cluster}/${name}"
    & $Aria --continue=true --max-connection-per-server=4 --split=4 --min-split-size=1M --file-allocation=none --summary-interval=15 --dir $data --out $name $url | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "aria2 download failed for $Cluster" }
    if ((Get-Item -LiteralPath $path).Length -ne $ExpectedSize) { throw "Incomplete asset: $name" }
    return $path
}

foreach ($cluster in $Clusters) {
    $image = Download-Asset $cluster 'f606w_v2_img.fits' $expectedBytes
    $catalogue = Download-Asset $cluster 'r.rdviq.cal.adj.zpt' $catalogueBytes[$cluster]
    Assert-ValidFits $image
    # Photutils emits non-fatal convergence warnings on stderr.  Preserve them
    # in the run log, but decide success solely from Python's exit code.
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & python (Join-Path $Root 'experiments\hst\acsggct_expanded_high_density_precision.py') --clusters $cluster --output-dir $results 2>&1 | Out-File -FilePath (Join-Path $results "${cluster}_run.log") -Append -Encoding utf8
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $oldPreference
    if ($exitCode -ne 0) { throw "Evaluation failed for $cluster" }
}
