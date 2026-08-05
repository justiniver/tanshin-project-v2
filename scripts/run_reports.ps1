[CmdletBinding()]
param(
    [Parameter(
        Mandatory = $true,
        Position = 0,
        ValueFromRemainingArguments = $true
    )]
    [string[]]$SecurityCode,

    [switch]$PreviewOnly,

    [switch]$FlashTranslation,

    [switch]$ProTranslation,

    [switch]$Pro,

    [switch]$Sol
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

$literalProFlags = @(
    $SecurityCode | Where-Object { $_ -ieq '--pro' }
)
$literalFlashTranslationFlags = @(
    $SecurityCode | Where-Object { $_ -ieq '--flash-translation' }
)
$literalProTranslationFlags = @(
    $SecurityCode | Where-Object { $_ -ieq '--pro-translation' }
)
$literalSolFlags = @(
    $SecurityCode | Where-Object { $_ -ieq '--sol' }
)
if ($literalProFlags.Count -gt 1) {
    throw "The --pro option may be supplied only once."
}
if ($literalSolFlags.Count -gt 1) {
    throw "The --sol option may be supplied only once."
}
if ($literalProTranslationFlags.Count -gt 1) {
    throw "The --pro-translation option may be supplied only once."
}
if ($literalFlashTranslationFlags.Count -gt 1) {
    throw "The --flash-translation option may be supplied only once."
}
$usePro = $Pro -or $literalProFlags.Count -eq 1
$useFlashTranslation = (
    $FlashTranslation -or $literalFlashTranslationFlags.Count -eq 1
)
$useProTranslation = (
    $ProTranslation -or $literalProTranslationFlags.Count -eq 1
)
$useSol = $Sol -or $literalSolFlags.Count -eq 1
$selectedProfileCount = @(
    $usePro,
    $useFlashTranslation,
    $useProTranslation,
    $useSol
).Where({ $_ }).Count
if ($selectedProfileCount -gt 1) {
    throw (
        "Choose only one of --flash-translation, --pro-translation, " +
        "--pro, or --sol."
    )
}
$securityCodeArguments = @(
    $SecurityCode |
        Where-Object {
            $_ -ine '--pro' -and
            $_ -ine '--flash-translation' -and
            $_ -ine '--pro-translation' -and
            $_ -ine '--sol'
        }
)
$modelProfile = if ($useSol) {
    'sol'
} elseif ($usePro) {
    'pro'
} elseif ($useProTranslation) {
    'pro-translation'
} elseif ($useFlashTranslation) {
    'flash-translation'
} else {
    'default'
}

function Get-NormalizedSecurityCodes {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string[]]$Values
    )

    $codes = @(
        foreach ($value in $Values) {
            foreach ($part in ($value -split ',')) {
                $trimmed = $part.Trim()
                if (-not [string]::IsNullOrWhiteSpace($trimmed)) {
                    $trimmed
                }
            }
        }
    )
    if ($codes.Count -eq 0) {
        throw 'Provide at least one four-digit security code.'
    }
    foreach ($code in $codes) {
        if ($code -notmatch '^\d{4}$') {
            throw "Invalid security code '$code'. Use a four-digit ticker."
        }
    }
    $duplicates = @(
        $codes |
            Group-Object |
            Where-Object { $_.Count -gt 1 } |
            ForEach-Object { $_.Name }
    )
    if ($duplicates.Count -gt 0) {
        throw (
            'Duplicate security codes are not allowed: ' +
            ($duplicates -join ', ')
        )
    }
    return $codes
}

function Read-YesNo {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Prompt
    )

    while ($true) {
        $answer = (Read-Host "$Prompt (y/n)").Trim().ToLowerInvariant()
        if ($answer -eq 'y') {
            return $true
        }
        if ($answer -eq 'n') {
            return $false
        }
        Write-Host "Please enter 'y' or 'n'."
    }
}

function Wait-ForAnalysisCooldown {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [datetime]$PreviousAnalysisCompletedAt,

        [Parameter(Mandatory)]
        [ValidateRange(1, 3600)]
        [int]$CooldownSeconds
    )

    $elapsedSeconds = (
        (Get-Date) - $PreviousAnalysisCompletedAt
    ).TotalSeconds
    $remainingSeconds = [Math]::Ceiling(
        [Math]::Max(0, $CooldownSeconds - $elapsedSeconds)
    )
    if ($remainingSeconds -le 0) {
        Write-Host (
            "ANALYSIS COOLDOWN: already satisfied " +
            "($CooldownSeconds seconds elapsed)."
        )
        return
    }
    Write-Host ''
    Write-Host (
        "ANALYSIS COOLDOWN: waiting $remainingSeconds seconds before the " +
        "next company analysis."
    )
    Start-Sleep -Seconds $remainingSeconds
}

function Invoke-OfflinePreparation {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Code,

        [Parameter(Mandatory)]
        [ValidateSet('analysis', 'translation')]
        [string]$Stage,

        [Parameter(Mandatory)]
        [string]$OutputRoot,

        [Parameter(Mandatory)]
        [ValidateSet(
            'default',
            'flash-translation',
            'pro-translation',
            'pro',
            'sol'
        )]
        [string]$ModelProfile
    )

    $env:TANSHIN_OFFLINE_ONLY = '1'
    $output = & $python -m tanshin_pipeline $Code `
        --repository-root $repositoryRoot `
        --output-root $OutputRoot `
        --stage $Stage `
        --model-profile $ModelProfile `
        --max-api-attempts 1 2>&1
    $preparationExitCode = $LASTEXITCODE
    if ($preparationExitCode -ne 0) {
        $output | ForEach-Object { Write-Host $_ }
        throw (
            "Offline $Stage preparation failed for $Code with exit code " +
            "$preparationExitCode. No API request was sent."
        )
    }
}

function Get-AnalysisPreparation {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Code,

        [Parameter(Mandatory)]
        [string]$OutputRoot,

        [Parameter(Mandatory)]
        [ValidateSet(
            'default',
            'flash-translation',
            'pro-translation',
            'pro',
            'sol'
        )]
        [string]$ModelProfile
    )

    Invoke-OfflinePreparation `
        -Code $Code `
        -Stage analysis `
        -OutputRoot $OutputRoot `
        -ModelProfile $ModelProfile
    $artifacts = Join-Path $OutputRoot "$Code\artifacts"
    return [pscustomobject]@{
        Code = $Code
        Plan = (
            Get-Content -LiteralPath (
                Join-Path $artifacts 'request_plan_analysis.json'
            ) -Raw -Encoding UTF8 |
                ConvertFrom-Json
        )
        Cost = (
            Get-Content -LiteralPath (
                Join-Path $artifacts 'cost.json'
            ) -Raw -Encoding UTF8 |
                ConvertFrom-Json
        )
        Manifest = (
            Get-Content -LiteralPath (
                Join-Path $artifacts 'selection_manifest.json'
            ) -Raw -Encoding UTF8 |
                ConvertFrom-Json
        )
    }
}

function Write-AnalysisPreparation {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object]$Preparation
    )

    $code = $Preparation.Code
    $plan = $Preparation.Plan
    $cost = $Preparation.Cost
    $manifest = $Preparation.Manifest
    Write-Host ''
    Write-Host ('=' * 72)
    Write-Host "COMPANY: $code"
    Write-Host "Latest filing: $($manifest.latest_filename)"
    Write-Host "Model profile: $($plan.model_profile)"
    Write-Host "Analysis provider: $($plan.provider)"
    Write-Host "Analysis model: $($plan.model)"
    if ($plan.provider -eq 'openai') {
        Write-Host "PDF detail: $($plan.request_options.pdf_detail)"
    }
    Write-Host "Analysis request ID: $($plan.request_id)"
    if ($null -ne $plan.style_blueprint_path) {
        Write-Host "Fact-free style blueprint: $($plan.style_blueprint_path)"
        Write-Host "Blueprint SHA-256: $($plan.style_blueprint_sha256)"
    }
    Write-Host (
        'Estimated maximum analysis cost: JPY {0:N0}' -f
        $cost.analysis.maximum_stage_cost_jpy
    )
    Write-Host (
        'Estimated maximum optional translation cost: JPY {0:N0}' -f
        $cost.translation.maximum_stage_cost_jpy
    )
    Write-Host (
        'Yen conversion assumption: JPY {0:N0} per USD' -f
        $cost.usd_to_jpy_rate
    )
    Write-Host "PDFs submitted: $($plan.files.Count)"
    foreach ($file in $plan.files) {
        Write-Host "  - $($file.filename) ($($file.page_count) pages)"
    }
    Write-Host "Expected Japanese report: output\$code\analysis_ja_$code.md"
    Write-Host "Optional English report: output\$code\analysis_en_$code.md"
    Write-Host "Selection manifest: output\$code\artifacts\selection_manifest.json"
    Write-Host (
        'Analysis diagnostics: model_response_ja.raw.json, ' +
        'analysis_ja.structured.json, analysis_ja.normalized.json, ' +
        'normalization_ja.json, management_consistency.json, ' +
        'validation_ja.json, report_status_ja.json, ' +
        'api_status_analysis.json, token_usage.json, cost.json, and ' +
        'exemplar_comparison_ja.json'
    )
}

function Backup-CurrentOutput {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Code,

        [Parameter(Mandatory)]
        [string]$BatchStamp
    )

    $currentOutput = Join-Path $repositoryRoot "output\$Code"
    if (-not (Test-Path -LiteralPath $currentOutput -PathType Container)) {
        return
    }
    $historyRoot = Join-Path $currentOutput 'history'
    $archive = Join-Path $historyRoot $BatchStamp
    New-Item -ItemType Directory -Path $archive -Force | Out-Null
    Get-ChildItem -LiteralPath $currentOutput -Force |
        Where-Object { $_.Name -ne 'history' } |
        ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $archive -Recurse
        }
    Write-Host "Archived existing $Code output to: $archive"
}

function Invoke-LiveStage {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Code,

        [Parameter(Mandatory)]
        [ValidateSet('analysis', 'translation')]
        [string]$Stage,

        [Parameter(Mandatory)]
        [string]$RequestId,

        [Parameter(Mandatory)]
        [ValidateSet(
            'default',
            'flash-translation',
            'pro-translation',
            'pro',
            'sol'
        )]
        [string]$ModelProfile
    )

    Remove-Item Env:TANSHIN_OFFLINE_ONLY -ErrorAction SilentlyContinue
    $env:TANSHIN_LIVE_API = 'MANUAL_USER_RUN'
    Write-Host ''
    Write-Host ('=' * 72)
    Write-Host "COMPANY: $Code"
    Write-Host "STAGE: $($Stage.ToUpperInvariant())"
    Write-Host 'MODEL RUN STATE: RUNNING'
    & $python -m tanshin_pipeline $Code `
        --repository-root $repositoryRoot `
        --stage $Stage `
        --model-profile $ModelProfile `
        --execute-api `
        --confirm-request $RequestId `
        --max-api-attempts 1
    $executionExitCode = $LASTEXITCODE

    $statusName = if ($Stage -eq 'analysis') {
        'api_status_analysis.json'
    } else {
        'api_status_translation.json'
    }
    $statusPath = Join-Path (
        Join-Path $repositoryRoot "output\$Code\artifacts"
    ) $statusName
    if (-not (Test-Path -LiteralPath $statusPath -PathType Leaf)) {
        Write-Host 'MODEL RUN STATE: UNKNOWN (no status artifact)'
        Write-Host 'REPORT PIPELINE STATE: NOT_COMPLETED'
        return $false
    }
    $apiStatus = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    Write-ApiStatusSummary `
        -ApiStatus $apiStatus `
        -ExpectedRunId $RequestId `
        -ExecutionExitCode $executionExitCode
    $state = Get-OptionalJsonProperty -InputObject $apiStatus -Name 'state'
    return ($state -eq 'SUCCESS' -and $executionExitCode -eq 0)
}

function Get-TranslationPreparation {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Code,

        [Parameter(Mandatory)]
        [ValidateSet(
            'default',
            'flash-translation',
            'pro-translation',
            'pro',
            'sol'
        )]
        [string]$ModelProfile
    )

    $canonicalOutput = Join-Path $repositoryRoot 'output'
    Invoke-OfflinePreparation `
        -Code $Code `
        -Stage translation `
        -OutputRoot $canonicalOutput `
        -ModelProfile $ModelProfile
    $artifacts = Join-Path $canonicalOutput "$Code\artifacts"
    return [pscustomobject]@{
        Code = $Code
        Plan = (
            Get-Content -LiteralPath (
                Join-Path $artifacts 'request_plan_translation.json'
            ) -Raw -Encoding UTF8 |
                ConvertFrom-Json
        )
        Cost = (
            Get-Content -LiteralPath (
                Join-Path $artifacts 'cost.json'
            ) -Raw -Encoding UTF8 |
                ConvertFrom-Json
        )
    }
}

function Write-TranslationPreparation {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object]$Preparation
    )

    $code = $Preparation.Code
    $plan = $Preparation.Plan
    $cost = $Preparation.Cost
    Write-Host ''
    Write-Host "ENGLISH REQUEST READY: $code"
    Write-Host "Model profile: $($plan.model_profile)"
    Write-Host "Translation provider: $($plan.provider)"
    Write-Host "Translation model: $($plan.model)"
    Write-Host "Translation request ID: $($plan.request_id)"
    Write-Host (
        'Estimated maximum translation cost: JPY {0:N0}' -f
        $cost.translation.maximum_stage_cost_jpy
    )
    Write-Host (
        'Yen conversion assumption: JPY {0:N0} per USD' -f
        $cost.usd_to_jpy_rate
    )
    Write-Host 'PDFs submitted: none (validated Japanese structured JSON only)'
    Write-Host "Expected English report: output\$code\analysis_en_$code.md"
    Write-Host (
        'Translation diagnostics: model_response_en.raw.json, ' +
        'analysis_en.structured.json, analysis_en.normalized.json, ' +
        'normalization_en.json, validation_en.json, report_status_en.json, ' +
        'api_status_translation.json, token_usage.json, cost.json, and ' +
        'exemplar_comparison_en.json'
    )
}

$codes = @(Get-NormalizedSecurityCodes -Values $securityCodeArguments)
$oldOffline = $env:TANSHIN_OFFLINE_ONLY
$oldLive = $env:TANSHIN_LIVE_API
$preflightParent = Join-Path $repositoryRoot 'tmp'
$preflightRoot = Join-Path (
    $preflightParent
) ("batch_preflight_" + [guid]::NewGuid().ToString('N'))
$analysisCooldownSeconds = 75

function Invoke-ReportBatch {
    try {
        New-Item -ItemType Directory -Path $preflightRoot -Force | Out-Null
        Write-Host 'BATCH PREFLIGHT'
        Write-Host 'No API request is sent during this preparation.'
        Write-Host "Companies: $($codes -join ', ')"
        Write-Host "Model profile: $modelProfile"
        Write-Host (
            'The selection is all-or-nothing: there is no later per-company ' +
            'selection prompt.'
        )

        $analysisPreparations = @(
            foreach ($code in $codes) {
                Get-AnalysisPreparation -Code $code `
                    -OutputRoot $preflightRoot `
                    -ModelProfile $modelProfile
            }
        )
        foreach ($preparation in $analysisPreparations) {
            Write-AnalysisPreparation -Preparation $preparation
        }

        $maximumAnalysisCost = (
            $analysisPreparations |
                ForEach-Object { $_.Cost.analysis.maximum_stage_cost_jpy } |
                Measure-Object -Sum
        ).Sum
        $maximumTranslationCost = (
            $analysisPreparations |
                ForEach-Object { $_.Cost.translation.maximum_stage_cost_jpy } |
                Measure-Object -Sum
        ).Sum
        $maximumPages = (
            $analysisPreparations |
                ForEach-Object { $_.Manifest.total_selected_pages } |
                Measure-Object -Sum
        ).Sum
        Write-Host ''
        Write-Host ('=' * 72)
        Write-Host 'BATCH TOTALS'
        Write-Host "Companies: $($codes.Count)"
        Write-Host "Selected PDF pages: $maximumPages"
        Write-Host (
            'Maximum analysis-only cost: JPY {0:N0}' -f
            $maximumAnalysisCost
        )
        Write-Host (
            'Maximum analysis-plus-English cost: JPY {0:N0}' -f
            ($maximumAnalysisCost + $maximumTranslationCost)
        )
        Write-Host (
            'Yen conversion assumption: JPY {0:N0} per USD' -f
            $analysisPreparations[0].Cost.usd_to_jpy_rate
        )
        Write-Host 'Each stage uses one API attempt; there are no automatic retries.'
        Write-Host (
            "Analysis requests use a $analysisCooldownSeconds-second cooldown; " +
            "time spent translating counts toward it."
        )

        if ($PreviewOnly) {
            Write-Host ''
            Write-Host 'PREVIEW ONLY: no API request was sent.'
            return 0
        }

        $proceed = Read-YesNo -Prompt (
            "Proceed with analysis for all $($codes.Count) companies"
        )
        if (-not $proceed) {
            Write-Host 'Cancelled. No API request was sent.'
            return 0
        }
        $includeEnglish = Read-YesNo -Prompt (
            "Generate an English report immediately after each company's analysis"
        )

        $requestCount = $codes.Count
        if ($includeEnglish) {
            $requestCount += $codes.Count
        }
        Write-Host ''
        Write-Host (
            "Authorized batch: $requestCount manually initiated API requests " +
            "across $($codes.Count) companies."
        )
        Write-Host (
            'Companies run sequentially: Japanese analysis, optional English ' +
            'translation, then the next company.'
        )

        if ($env:TANSHIN_TESTING -eq '1') {
            throw (
                'TANSHIN_TESTING=1 blocks live execution. Open a normal ' +
                'PowerShell session.'
            )
        }

        $batchStamp = Get-Date -Format 'yyyyMMdd_HHmmssfff'
        foreach ($code in $codes) {
            Backup-CurrentOutput -Code $code -BatchStamp $batchStamp
        }

        $previousAnalysisCompletedAt = $null
        foreach ($preparation in $analysisPreparations) {
            if ($null -ne $previousAnalysisCompletedAt) {
                Wait-ForAnalysisCooldown `
                    -PreviousAnalysisCompletedAt $previousAnalysisCompletedAt `
                    -CooldownSeconds $analysisCooldownSeconds
            }

            $analysisSucceeded = Invoke-LiveStage `
                -Code $preparation.Code `
                -Stage analysis `
                -RequestId $preparation.Plan.request_id `
                -ModelProfile $modelProfile
            $previousAnalysisCompletedAt = Get-Date
            if (-not $analysisSucceeded) {
                Write-Host ''
                Write-Host (
                    "BATCH STATE: ANALYSIS FAILED for $($preparation.Code)"
                )
                return 3
            }

            if ($includeEnglish) {
                Write-Host ''
                Write-Host (
                    "Japanese analysis succeeded for $($preparation.Code). " +
                    'Preparing its English request offline.'
                )
                $translationPreparation = Get-TranslationPreparation `
                    -Code $preparation.Code `
                    -ModelProfile $modelProfile
                Write-TranslationPreparation `
                    -Preparation $translationPreparation
                $translationSucceeded = Invoke-LiveStage `
                    -Code $translationPreparation.Code `
                    -Stage translation `
                    -RequestId $translationPreparation.Plan.request_id `
                    -ModelProfile $modelProfile
                if (-not $translationSucceeded) {
                    Write-Host ''
                    Write-Host (
                        "BATCH STATE: TRANSLATION FAILED for " +
                        $translationPreparation.Code
                    )
                    return 4
                }
            }
        }

        if (-not $includeEnglish) {
            Write-Host ''
            Write-Host 'BATCH STATE: SUCCESS (Japanese reports only)'
            return 0
        }

        Write-Host ''
        Write-Host 'BATCH STATE: SUCCESS'
        Write-Host "Japanese and English reports completed for: $($codes -join ', ')"
        return 0
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

        $resolvedParent = [IO.Path]::GetFullPath($preflightParent)
        $resolvedPreflight = [IO.Path]::GetFullPath($preflightRoot)
        if (
            (Test-Path -LiteralPath $resolvedPreflight -PathType Container) -and
            ([IO.Path]::GetDirectoryName($resolvedPreflight) -eq $resolvedParent)
        ) {
            Remove-Item -LiteralPath $resolvedPreflight -Recurse -Force
        }
    }
}

exit (Invoke-ReportBatch)
