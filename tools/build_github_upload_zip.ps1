param(
    [string]$OutputDirectory = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string]$PackageName = ("AstroCFR_GitHub_Upload_" + (Get-Date -Format 'yyyyMMdd'))
)

$ErrorActionPreference = 'Stop'
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$Stage = Join-Path $OutputDirectory $PackageName
$Zip = Join-Path $OutputDirectory ($PackageName + '.zip')

if ((Test-Path -LiteralPath $Stage) -or (Test-Path -LiteralPath $Zip)) {
    throw "Refusing to overwrite an existing package: $Stage or $Zip"
}
New-Item -ItemType Directory -Path $Stage | Out-Null

function Get-RelativePath {
    param([string]$Base, [string]$Target)
    $baseUri = [Uri]($Base.TrimEnd('\') + '\')
    $targetUri = [Uri]$Target
    return [Uri]::UnescapeDataString($baseUri.MakeRelativeUri($targetUri).ToString()).Replace('/', '\')
}

function Copy-RepoFile {
    param([string]$Source)
    $relative = Get-RelativePath $RepositoryRoot $Source
    $destination = Join-Path $Stage $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $destination
}

function Copy-SelectedTree {
    param([string]$RelativeDirectory, [scriptblock]$Include)
    $sourceDirectory = Join-Path $RepositoryRoot $RelativeDirectory
    Get-ChildItem -LiteralPath $sourceDirectory -Recurse -File | Where-Object {
        $_.FullName -notmatch '\\(__pycache__|\.git)\\' -and (& $Include $_)
    } | ForEach-Object { Copy-RepoFile $_.FullName }
}

# Repository metadata and runnable source.
@('.gitignore', 'CITATION.cff', 'LICENSE', 'README.md', 'RELEASE.md', 'pyproject.toml') |
    ForEach-Object { Copy-RepoFile (Join-Path $RepositoryRoot $_) }
foreach ($directory in @('configs', 'data', 'docs', 'environment', 'experiments', 'src', 'tools')) {
    Copy-SelectedTree $directory { param($file) $file.Extension -in @('.py', '.ps1', '.md', '.txt', '.yml', '.yaml', '.json', '.csv') }
}

# Keep only source/provenance material from the external tree.
Copy-RepoFile (Join-Path $RepositoryRoot 'external\README.md')

# Curated, machine-readable high-density evidence.  Do not bulk-copy the
# historical results tree: it contains superseded pilots and machine-local
# source paths that are neither needed nor suitable for a public release.
@(
    'results\acsggct11_csst4_all_methods_matrix.csv',
    'results\acsggct11_csst4_all_methods_matrix.md',
    'results\acsggct11_csst4_method_summary.csv',
    'results\acsggct11_csst4_method_registry.csv',
    'results\acsggct11_spatial_vs_photutils_by_field.csv',
    'results\high_density_manuscript_qa.md',
    'results\acsggct_all11_baselines\hst_unified_baseline_summary.json',
    'results\acsggct_all11_baselines\hst_unified_baseline_results.csv',
    'results\hst_literature_method_benchmark_all11\summary.csv',
    'results\hst_literature_method_benchmark_all11\same_star_pairs.csv',
    'results\hst_hybrid_wpdc_photutils\hybrid_summary.json',
    'results\csst_psfnet_audit\README.md',
    'results\csst_unified_five_methods\csst_unified_five_methods.csv',
    'results\csst_unified_five_methods\csst_unified_five_methods.json'
) | ForEach-Object { Copy-RepoFile (Join-Path $RepositoryRoot $_) }

# The upload package contains only the current manuscript pair, not local draft history.
Copy-RepoFile (Join-Path $RepositoryRoot 'supplementary\README.md')
Get-ChildItem -LiteralPath (Join-Path $RepositoryRoot 'supplementary') -File | Where-Object {
    $_.Name -in @('AstroCFR_Crowded_Field_Manuscript_v45_high_density_final.docx',
                  'AstroCFR_Supplementary_Materials_v45_high_density_final.docx') -or
    $_.Extension -in @('.md', '.txt', '.yml', '.yaml')
} | ForEach-Object { Copy-RepoFile $_.FullName }

$manifest = Join-Path $Stage 'PACKAGE_MANIFEST_SHA256.txt'
Get-ChildItem -LiteralPath $Stage -Recurse -File | Sort-Object FullName | ForEach-Object {
    $relative = (Get-RelativePath $Stage $_.FullName).Replace('\', '/')
    "$( (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash )  $relative"
} | Set-Content -LiteralPath $manifest -Encoding utf8

$pathLeak = Get-ChildItem -LiteralPath $Stage -Recurse -File | Where-Object {
    $_.Extension -in @('.py', '.ps1', '.md', '.txt', '.json', '.csv', '.yml', '.yaml') -and
    $_.Name -ne 'build_github_upload_zip.ps1'
} | Select-String -Pattern 'C:\\Users\\|D:\\test-process\\|/home/' -SimpleMatch:$false
if ($pathLeak) {
    $pathLeak | Select-Object -First 10 | Format-Table Path, LineNumber, Line -AutoSize
    throw 'The package contains a local absolute path. Resolve it before uploading.'
}

Compress-Archive -LiteralPath $Stage -DestinationPath $Zip -CompressionLevel Optimal
$zipHash = (Get-FileHash -LiteralPath $Zip -Algorithm SHA256).Hash
$fileCount = (Get-ChildItem -LiteralPath $Stage -Recurse -File).Count
[PSCustomObject]@{
    Archive = $Zip
    ArchiveMB = [math]::Round((Get-Item -LiteralPath $Zip).Length / 1MB, 2)
    SHA256 = $zipHash
    Files = $fileCount
} | Format-List
