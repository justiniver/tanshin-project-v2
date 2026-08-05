[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidatePattern('^\d{4}$')]
    [string]$SecurityCode = '1808',

    [Parameter(Position = 1)]
    [ValidateSet('analysis', 'translation')]
    [string]$Stage = 'analysis',

    [switch]$Execute,

    [switch]$Pro,

    [switch]$Key2Translation,

    [switch]$ProTranslation
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$statusHelpers = Join-Path $PSScriptRoot 'api_status_helpers.ps1'
. $statusHelpers
$python = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python virtual environment was not found at: $python"
}

$oldOffline = $env:TANSHIN_OFFLINE_ONLY
$oldLive = $env:TANSHIN_LIVE_API
$selectedProfileCount = @(
    $Pro,
    $Key2Translation,
    $ProTranslation
).Where({ $_ }).Count
if ($selectedProfileCount -gt 1) {
    throw "Choose only one of -Key2Translation, -ProTranslation, or -Pro."
}
$modelProfile = if ($Pro) {
    'pro'
} elseif ($ProTranslation) {
    'pro-translation'
} elseif ($Key2Translation) {
    'key2-translation'
} else {
    'default'
}

try {
    if ($Execute) {
        $currentOutput = Join-Path $repositoryRoot "output\$SecurityCode"
        if (Test-Path -LiteralPath $currentOutput -PathType Container) {
            $historyRoot = Join-Path $currentOutput 'history'
            $archive = Join-Path $historyRoot (Get-Date -Format 'yyyyMMdd_HHmmss')
            New-Item -ItemType Directory -Path $archive -Force | Out-Null
            Get-ChildItem -LiteralPath $currentOutput -Force |
                Where-Object { $_.Name -ne 'history' } |
                ForEach-Object {
                    Copy-Item -LiteralPath $_.FullName -Destination $archive -Recurse
                }
            Write-Host "Archived existing output to: $archive"
        }
    }

    # Always regenerate and display the inspected request plan offline first.
    $env:TANSHIN_OFFLINE_ONLY = '1'
    & $python -m tanshin_pipeline $SecurityCode `
        --repository-root $repositoryRoot `
        --stage $Stage `
        --model-profile $modelProfile `
        --max-api-attempts 1
    if ($LASTEXITCODE -ne 0) {
        throw "Offline preparation failed with exit code $LASTEXITCODE."
    }

    $artifacts = Join-Path $repositoryRoot "output\$SecurityCode\artifacts"
    $planName = if ($Stage -eq 'analysis') {
        'request_plan_analysis.json'
    } else {
        'request_plan_translation.json'
    }
    $planPath = Join-Path $artifacts $planName
    $costPath = Join-Path $artifacts 'cost.json'
    $plan = Get-Content -LiteralPath $planPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $cost = Get-Content -LiteralPath $costPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $stageCost = if ($Stage -eq 'analysis') {
        $cost.analysis.maximum_stage_cost_jpy
    } else {
        $cost.translation.maximum_stage_cost_jpy
    }

    Write-Host ''
    Write-Host 'MANUAL API REQUEST SUMMARY'
    Write-Host "Company: $SecurityCode"
    Write-Host "Stage: $Stage"
    Write-Host "Model profile: $($plan.model_profile)"
    Write-Host "Provider: $($plan.provider)"
    Write-Host "Model: $($plan.model)"
    Write-Host "Request ID: $($plan.request_id)"
    if ($null -ne $plan.style_blueprint_path) {
        Write-Host "Fact-free style blueprint: $($plan.style_blueprint_path)"
        Write-Host "Blueprint SHA-256: $($plan.style_blueprint_sha256)"
    }
    Write-Host ('Estimated maximum stage cost: JPY {0:N0}' -f $stageCost)
    if ($modelProfile -eq 'default') {
        Write-Host (
            'Billing note: This profile uses only GEMINI_API_KEY and should ' +
            "be free when that key's project is eligible for the Gemini free " +
            'tier; the JPY estimate is a paid-tier upper bound.'
        )
    }
    if ($Stage -eq 'analysis') {
        Write-Host "PDFs submitted: $($plan.files.Count)"
        foreach ($file in $plan.files) {
            Write-Host "  - $($file.filename) ($($file.page_count) pages)"
        }
        Write-Host "Expected final on success: output\$SecurityCode\analysis_ja_$SecurityCode.md"
        Write-Host 'Validation findings are retained in JSON diagnostics and do not change the Markdown filename.'
        Write-Host "Diagnostics: output\$SecurityCode\artifacts\model_response_ja.raw.json,"
        Write-Host '  analysis_ja.structured.json, analysis_ja.normalized.json,'
        Write-Host '  normalization_ja.json, management_consistency.json,'
        Write-Host '  validation_ja.json, report_status_ja.json,'
        Write-Host '  api_status_analysis.json, token_usage.json, cost.json,'
        Write-Host '  and exemplar_comparison_ja.json'
    } else {
        Write-Host 'PDFs submitted: none (validated Japanese structured JSON only)'
        Write-Host "Expected final on success: output\$SecurityCode\analysis_en_$SecurityCode.md"
        Write-Host 'Validation findings are retained in JSON diagnostics and do not change the Markdown filename.'
        Write-Host "Diagnostics: output\$SecurityCode\artifacts\model_response_en.raw.json,"
        Write-Host '  analysis_en.structured.json, analysis_en.normalized.json,'
        Write-Host '  normalization_en.json, validation_en.json, report_status_en.json,'
        Write-Host '  api_status_translation.json, token_usage.json, cost.json,'
        Write-Host '  and exemplar_comparison_en.json'
    }

    if (-not $Execute) {
        Write-Host ''
        Write-Host 'No API request was sent.'
        Write-Host 'To manually initiate this exact request, rerun with -Execute:'
        $profileArgument = if ($Pro) {
            ' -Pro'
        } elseif ($ProTranslation) {
            ' -ProTranslation'
        } elseif ($Key2Translation) {
            ' -Key2Translation'
        } else {
            ''
        }
        Write-Host (
            ".\scripts\run_gemini_stage.ps1 $SecurityCode $Stage" +
            "$profileArgument -Execute"
        )
        exit 0
    }

    if ($env:TANSHIN_TESTING -eq '1') {
        throw 'TANSHIN_TESTING=1 blocks live execution. Open a normal PowerShell session.'
    }

    $requiredConfirmation = "RUN $SecurityCode $($Stage.ToUpper()) $($plan.request_id)"
    Write-Host ''
    $confirmation = Read-Host "Type exactly '$requiredConfirmation' to send one request"
    if ($confirmation -cne $requiredConfirmation) {
        Write-Host 'Confirmation did not match. No API request was sent.'
        exit 2
    }

    # The user has manually initiated and confirmed this one inspected request.
    Remove-Item Env:TANSHIN_OFFLINE_ONLY -ErrorAction SilentlyContinue
    $env:TANSHIN_LIVE_API = 'MANUAL_USER_RUN'
    Write-Host ''
    Write-Host 'MODEL RUN STATE: RUNNING'
    & $python -m tanshin_pipeline $SecurityCode `
        --repository-root $repositoryRoot `
        --stage $Stage `
        --model-profile $modelProfile `
        --execute-api `
        --confirm-request $plan.request_id `
        --max-api-attempts 1
    $executionExitCode = $LASTEXITCODE

    $statusName = if ($Stage -eq 'analysis') {
        'api_status_analysis.json'
    } else {
        'api_status_translation.json'
    }
    $statusPath = Join-Path $artifacts $statusName
    Write-Host ''
    if (Test-Path -LiteralPath $statusPath -PathType Leaf) {
        $apiStatus = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        Write-ApiStatusSummary `
            -ApiStatus $apiStatus `
            -ExpectedRunId $plan.request_id `
            -ExecutionExitCode $executionExitCode
    } else {
        Write-Host 'MODEL RUN STATE: UNKNOWN (no status artifact)'
        Write-Host 'REPORT PIPELINE STATE: NOT_COMPLETED'
    }
}
finally {
    if ($null -eq $oldOffline) {
        Remove-Item Env:TANSHIN_OFFLINE_ONLY -ErrorAction SilentlyContinue
    } else {
        $env:TANSHIN_OFFLINE_ONLY = $oldOffline
    }
    if ($null -eq $oldLive) {
        Remove-Item Env:TANSHIN_LIVE_API -ErrorAction SilentlyContinue
    } else {
        $env:TANSHIN_LIVE_API = $oldLive
    }
}

exit $executionExitCode
