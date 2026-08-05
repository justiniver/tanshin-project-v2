[CmdletBinding()]
param(
    [switch]$SkipModelDownload
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$primaryPython = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
$experimentsDir = Join-Path $repositoryRoot 'output\experiments'
$doclingVenv = Join-Path $experimentsDir 'docling_venv'
$doclingPython = Join-Path $doclingVenv 'Scripts\python.exe'
$doclingTools = Join-Path $doclingVenv 'Scripts\docling-tools.exe'
$requirements = Join-Path (
    $repositoryRoot
) 'experiments\docling_text_pipeline\requirements.txt'
$modelsDir = Join-Path (
    $repositoryRoot
) 'output\experiments\docling_models'
$environmentLock = Join-Path (
    $experimentsDir
) 'docling_environment.freeze.txt'

if (-not (Test-Path -LiteralPath $primaryPython -PathType Leaf)) {
    throw "Primary project Python was not found at: $primaryPython"
}

if (-not (Test-Path -LiteralPath $doclingPython -PathType Leaf)) {
    New-Item -ItemType Directory -Path $experimentsDir -Force | Out-Null
    Write-Host "Creating disposable parser environment: $doclingVenv"
    & $primaryPython -m venv $doclingVenv
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not create the disposable Docling environment.'
    }
}

Write-Host 'Installing the pinned Docling experiment dependency...'
& $doclingPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw 'Could not upgrade pip in the disposable environment.'
}
& $doclingPython -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw 'Could not install the Docling experiment dependency.'
}
& $doclingPython -m pip freeze |
    Set-Content -LiteralPath $environmentLock -Encoding UTF8
if ($LASTEXITCODE -ne 0) {
    throw 'Could not record the disposable environment package lock.'
}

if (-not $SkipModelDownload) {
    if (-not (Test-Path -LiteralPath $doclingTools -PathType Leaf)) {
        throw "Docling model utility was not found at: $doclingTools"
    }
    New-Item -ItemType Directory -Path $modelsDir -Force | Out-Null
    Write-Host "Downloading local layout and table models to: $modelsDir"
    & $doclingTools models download layout tableformer -o $modelsDir
    if ($LASTEXITCODE -ne 0) {
        throw (
            'Docling installed, but its local models were not downloaded. ' +
            'Rerun this setup command later.'
        )
    }
}

Write-Host ''
Write-Host "Parser environment: $doclingVenv"
$modelFiles = @()
if (Test-Path -LiteralPath $modelsDir -PathType Container) {
    $modelFiles = @(
        Get-ChildItem -LiteralPath $modelsDir -Recurse -File |
            Select-Object -First 1
    )
}
if ($modelFiles.Count -eq 0) {
    Write-Host 'DOCLING ENVIRONMENT SETUP: COMPLETE'
    Write-Warning (
        'Local layout/table models are not ready. Rerun without ' +
        '-SkipModelDownload before previewing reports.'
    )
} else {
    Write-Host 'DOCLING EXPERIMENT SETUP: SUCCESS'
    Write-Host "Local model directory: $modelsDir"
    Write-Host "Environment lock: $environmentLock"
    Write-Host (
        'Preview the experiment with: ' +
        '.\scripts\run_docling_reports.ps1 6777 -PreviewOnly'
    )
}
