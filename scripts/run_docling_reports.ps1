[CmdletBinding()]
param(
    [Parameter(
        Mandatory = $true,
        Position = 0,
        ValueFromRemainingArguments = $true
    )]
    [string[]]$SecurityCode,

    [switch]$PreviewOnly,

    [switch]$ForceReparse,

    [switch]$Key2Translation,

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
$doclingPython = Join-Path (
    $repositoryRoot
) 'output\experiments\docling_venv\Scripts\python.exe'
$modelsDir = Join-Path (
    $repositoryRoot
) 'output\experiments\docling_models'
$outputRoot = Join-Path (
    $repositoryRoot
) 'output\experiments\docling_text'
$analysisCooldownSeconds = 75
$translationCooldownTokenThreshold = 225000

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Primary project Python was not found at: $python"
}
if (-not (Test-Path -LiteralPath $doclingPython -PathType Leaf)) {
    throw (
        "The disposable Docling environment is not set up. Run: " +
        '.\scripts\setup_docling_experiment.ps1'
    )
}
if (-not (Test-Path -LiteralPath $modelsDir -PathType Container)) {
    throw (
        "The local Docling models are not set up. Run: " +
        '.\scripts\setup_docling_experiment.ps1'
    )
}

$literalFlags = @{
    Pro = @($SecurityCode | Where-Object { $_ -ieq '--pro' })
    Key2Translation = @(
        $SecurityCode |
            Where-Object { $_ -ieq '--key2-translation' }
    )
    ProTranslation = @(
        $SecurityCode |
            Where-Object { $_ -ieq '--pro-translation' }
    )
    Sol = @($SecurityCode | Where-Object { $_ -ieq '--sol' })
}
foreach ($name in $literalFlags.Keys) {
    if ($literalFlags[$name].Count -gt 1) {
        throw "The corresponding model option may be supplied only once."
    }
}
$usePro = $Pro -or $literalFlags.Pro.Count -eq 1
$useKey2Translation = (
    $Key2Translation -or $literalFlags.Key2Translation.Count -eq 1
)
$useProTranslation = (
    $ProTranslation -or $literalFlags.ProTranslation.Count -eq 1
)
$useSol = $Sol -or $literalFlags.Sol.Count -eq 1
$selectedProfileCount = @(
    $usePro,
    $useKey2Translation,
    $useProTranslation,
    $useSol
).Where({ $_ }).Count
if ($selectedProfileCount -gt 1) {
    throw (
        'Choose only one of --key2-translation, --pro-translation, ' +
        '--pro, or --sol.'
    )
}
$modelProfile = if ($useSol) {
    'sol'
} elseif ($usePro) {
    'pro'
} elseif ($useProTranslation) {
    'pro-translation'
} elseif ($useKey2Translation) {
    'key2-translation'
} else {
    'default'
}
$securityCodeArguments = @(
    $SecurityCode |
        Where-Object {
            $_ -ine '--pro' -and
            $_ -ine '--key2-translation' -and
            $_ -ine '--pro-translation' -and
            $_ -ine '--sol'
        }
)

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
            throw "Invalid security code '$code'."
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
        [datetime]$PreviousAnalysisCompletedAt
    )

    $elapsed = ((Get-Date) - $PreviousAnalysisCompletedAt).TotalSeconds
    $remaining = [Math]::Ceiling(
        [Math]::Max(0, $analysisCooldownSeconds - $elapsed)
    )
    if ($remaining -gt 0) {
        Write-Host ''
        Write-Host (
            "ANALYSIS COOLDOWN: waiting $remaining seconds before the " +
            'next company.'
        )
        Start-Sleep -Seconds $remaining
    }
}

function Wait-ForTranslationCooldown {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [datetime]$AnalysisCompletedAt,

        [Parameter(Mandatory)]
        [long]$EstimatedTokenLoad
    )

    $elapsed = ((Get-Date) - $AnalysisCompletedAt).TotalSeconds
    $remaining = [Math]::Ceiling(
        [Math]::Max(0, $analysisCooldownSeconds - $elapsed)
    )
    if ($remaining -le 0) {
        Write-Host (
            "SAME-CREDENTIAL COOLDOWN: already satisfied " +
            "($analysisCooldownSeconds seconds elapsed)."
        )
        return
    }
    Write-Host ''
    Write-Host (
        "SAME-CREDENTIAL COOLDOWN: estimated two-stage load " +
        "$EstimatedTokenLoad tokens meets the conservative " +
        "$translationCooldownTokenThreshold-token threshold; waiting " +
        "$remaining seconds before translation."
    )
    Start-Sleep -Seconds $remaining
}

function Get-SameCredentialTokenLoad {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object]$AnalysisPreparation,

        [object]$TranslationPreparation
    )

    $translationCost = if ($null -eq $TranslationPreparation) {
        $AnalysisPreparation.Cost.translation
    } else {
        $TranslationPreparation.Cost.translation
    }
    return (
        [long]$AnalysisPreparation.Cost.analysis.estimated_input_tokens +
        [long]$AnalysisPreparation.Cost.analysis.maximum_output_tokens +
        [long]$translationCost.estimated_input_tokens +
        [long]$translationCost.maximum_output_tokens
    )
}

function Test-TranslationCooldownRequired {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object]$AnalysisPreparation,

        [Parameter(Mandatory)]
        [object]$TranslationPreparation
    )

    if (
        $AnalysisPreparation.Plan.provider -ne 'gemini' -or
        $TranslationPreparation.Plan.provider -ne 'gemini'
    ) {
        return $false
    }
    if (
        $AnalysisPreparation.Plan.provider_profile -ne
        $TranslationPreparation.Plan.provider_profile
    ) {
        return $false
    }
    return (
        (
            Get-SameCredentialTokenLoad `
                -AnalysisPreparation $AnalysisPreparation `
                -TranslationPreparation $TranslationPreparation
        ) -ge
        $translationCooldownTokenThreshold
    )
}

function Invoke-OfflinePreparation {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Code,

        [Parameter(Mandatory)]
        [ValidateSet('analysis', 'translation')]
        [string]$Stage,

        [switch]$Reparse
    )

    $arguments = @(
        '-m',
        'experiments.docling_text_pipeline',
        $Code,
        '--repository-root',
        $repositoryRoot,
        '--output-root',
        $outputRoot,
        '--docling-python',
        $doclingPython,
        '--models-dir',
        $modelsDir,
        '--stage',
        $Stage,
        '--model-profile',
        $modelProfile,
        '--max-api-attempts',
        '1'
    )
    if ($Reparse -and $Stage -eq 'analysis') {
        $arguments += '--force-reparse'
    }
    $env:TANSHIN_OFFLINE_ONLY = '1'
    $commandOutput = @(& $python @arguments 2>&1)
    $executionExitCode = $LASTEXITCODE
    foreach ($line in $commandOutput) {
        Write-Host $line
    }
    if ($executionExitCode -ne 0) {
        throw (
            "Offline $Stage preparation failed for $Code. " +
            'No AI request was sent.'
        )
    }
}

function Get-Preparation {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Code
    )

    Invoke-OfflinePreparation `
        -Code $Code `
        -Stage analysis `
        -Reparse:$ForceReparse
    $artifacts = Join-Path $outputRoot "$Code\artifacts"
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
        Extraction = (
            Get-Content -LiteralPath (
                Join-Path $artifacts 'docling_extraction_manifest.json'
            ) -Raw -Encoding UTF8 |
                ConvertFrom-Json
        )
        Comparison = (
            Get-Content -LiteralPath (
                Join-Path $artifacts 'input_size_comparison.json'
            ) -Raw -Encoding UTF8 |
                ConvertFrom-Json
        )
        Audit = (
            Get-Content -LiteralPath (
                Join-Path $artifacts 'docling_extraction_audit.json'
            ) -Raw -Encoding UTF8 |
                ConvertFrom-Json
        )
    }
}

function Write-Preparation {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object]$Preparation
    )

    Write-Host ''
    Write-Host ('=' * 72)
    Write-Host "COMPANY: $($Preparation.Code)"
    Write-Host 'EXPERIMENT: DOCLING MARKDOWN INPUT'
    Write-Host "Latest filing: $($Preparation.Manifest.latest_filename)"
    Write-Host "Model profile: $($Preparation.Plan.model_profile)"
    Write-Host "Analysis provider: $($Preparation.Plan.provider)"
    Write-Host "Analysis model: $($Preparation.Plan.model)"
    Write-Host "Analysis request ID: $($Preparation.Plan.request_id)"
    Write-Host 'PDF bytes sent to the model: 0'
    Write-Host (
        "PDFs parsed locally: $($Preparation.Manifest.selected_files.Count) " +
        "($($Preparation.Manifest.total_selected_pages) pages)"
    )
    Write-Host (
        'Docling Markdown characters: {0:N0}' -f
        $Preparation.Comparison.docling_corpus_characters
    )
    Write-Host (
        'Estimated text corpus tokens: {0:N0}' -f
        $Preparation.Comparison.estimated_docling_corpus_tokens
    )
    Write-Host (
        'Current PDF planning tokens: {0:N0}' -f
        $Preparation.Comparison.current_pdf_planning_tokens
    )
    Write-Host (
        'Text/PDF planning-token ratio: {0:N2}x' -f
        $Preparation.Comparison.text_to_pdf_planning_token_ratio
    )
    Write-Host (
        "Extraction audit warnings: $($Preparation.Audit.warning_count)"
    )
    Write-Host (
        'Estimated maximum analysis cost: JPY {0:N0}' -f
        $Preparation.Cost.analysis.maximum_stage_cost_jpy
    )
    Write-Host (
        'Estimated maximum optional translation cost: JPY {0:N0}' -f
        $Preparation.Cost.translation.maximum_stage_cost_jpy
    )
    Write-Host (
        'Experimental Japanese report: ' +
        "output\experiments\docling_text\$($Preparation.Code)\" +
        "analysis_ja_$($Preparation.Code).md"
    )
    Write-Host (
        'Optional English report: ' +
        "output\experiments\docling_text\$($Preparation.Code)\" +
        "analysis_en_$($Preparation.Code).md"
    )
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
        [string]$RequestId
    )

    Remove-Item Env:TANSHIN_OFFLINE_ONLY -ErrorAction SilentlyContinue
    $env:TANSHIN_LIVE_API = 'MANUAL_USER_RUN'
    Write-Host ''
    Write-Host ('=' * 72)
    Write-Host "COMPANY: $Code"
    Write-Host "STAGE: $($Stage.ToUpperInvariant())"
    Write-Host 'INPUT MODE: DOCLING MARKDOWN'
    Write-Host 'MODEL RUN STATE: RUNNING'

    $commandOutput = @(
        & $python -m experiments.docling_text_pipeline $Code `
            --repository-root $repositoryRoot `
            --output-root $outputRoot `
            --docling-python $doclingPython `
            --models-dir $modelsDir `
            --stage $Stage `
            --model-profile $modelProfile `
            --execute-api `
            --confirm-request $RequestId `
            --max-api-attempts 1 2>&1
    )
    $executionExitCode = $LASTEXITCODE
    foreach ($line in $commandOutput) {
        Write-Host $line
    }

    $statusName = if ($Stage -eq 'analysis') {
        'api_status_analysis.json'
    } else {
        'api_status_translation.json'
    }
    $statusPath = Join-Path (
        Join-Path $outputRoot "$Code\artifacts"
    ) $statusName
    if (-not (Test-Path -LiteralPath $statusPath -PathType Leaf)) {
        Write-Host 'MODEL RUN STATE: UNKNOWN (no status artifact)'
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
        [string]$Code
    )

    Invoke-OfflinePreparation -Code $Code -Stage translation
    $artifacts = Join-Path $outputRoot "$Code\artifacts"
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

$codes = @(Get-NormalizedSecurityCodes -Values $securityCodeArguments)
$oldOffline = $env:TANSHIN_OFFLINE_ONLY
$oldLive = $env:TANSHIN_LIVE_API

try {
    Write-Host 'DOCLING TEXT-INPUT BATCH PREFLIGHT'
    Write-Host 'Local extraction and preparation do not send an AI request.'
    Write-Host "Companies: $($codes -join ', ')"
    Write-Host "Model profile: $modelProfile"
    Write-Host "Experimental output root: $outputRoot"
    Write-Host 'Normal output/{ticker} reports are not modified.'

    $preparations = @(
        foreach ($code in $codes) {
            Get-Preparation -Code $code
        }
    )
    foreach ($preparation in $preparations) {
        Write-Preparation -Preparation $preparation
    }

    $analysisCost = (
        $preparations |
            ForEach-Object { $_.Cost.analysis.maximum_stage_cost_jpy } |
            Measure-Object -Sum
    ).Sum
    $translationCost = (
        $preparations |
            ForEach-Object { $_.Cost.translation.maximum_stage_cost_jpy } |
            Measure-Object -Sum
    ).Sum
    Write-Host ''
    Write-Host ('=' * 72)
    Write-Host 'BATCH TOTALS'
    Write-Host "Companies: $($codes.Count)"
    Write-Host ('Maximum analysis cost: JPY {0:N0}' -f $analysisCost)
    Write-Host (
        'Maximum analysis-plus-English cost: JPY {0:N0}' -f
        ($analysisCost + $translationCost)
    )
    if ($modelProfile -eq 'default') {
        Write-Host (
            'Billing note: This profile uses only GEMINI_API_KEY and should ' +
            "be free when that key's project is eligible for the Gemini free " +
            'tier; the JPY estimate is a paid-tier upper bound.'
        )
    }
    Write-Host 'Each model stage uses one API attempt; no automatic retries.'
    if ($modelProfile -in @('default', 'pro')) {
        Write-Host (
            'A same-credential analysis-to-translation cooldown applies when ' +
            'the estimated combined two-stage token load reaches ' +
            "$translationCooldownTokenThreshold tokens (10% headroom below " +
            '250,000).'
        )
    } else {
        Write-Host (
            'No analysis-to-translation cooldown is needed because the ' +
            'stages use different providers or credentials.'
        )
    }

    if ($PreviewOnly) {
        Write-Host ''
        Write-Host 'PREVIEW ONLY: no AI request was sent.'
        exit 0
    }

    $proceed = Read-YesNo -Prompt (
        "Proceed with text-input analysis for all $($codes.Count) companies"
    )
    if (-not $proceed) {
        Write-Host 'Cancelled. No AI request was sent.'
        exit 0
    }
    $includeEnglish = Read-YesNo -Prompt (
        "Generate an English report after each Japanese report"
    )
    if ($env:TANSHIN_TESTING -eq '1') {
        throw 'TANSHIN_TESTING=1 blocks live execution.'
    }

    $previousAnalysisCompletedAt = $null
    foreach ($preparation in $preparations) {
        if ($null -ne $previousAnalysisCompletedAt) {
            Wait-ForAnalysisCooldown `
                -PreviousAnalysisCompletedAt $previousAnalysisCompletedAt
        }
        $analysisSucceeded = Invoke-LiveStage `
            -Code $preparation.Code `
            -Stage analysis `
            -RequestId $preparation.Plan.request_id
        $previousAnalysisCompletedAt = Get-Date
        if (-not $analysisSucceeded) {
            Write-Host "BATCH STATE: ANALYSIS FAILED for $($preparation.Code)"
            exit 3
        }

        if ($includeEnglish) {
            $translation = Get-TranslationPreparation `
                -Code $preparation.Code
            Write-Host ''
            Write-Host "ENGLISH REQUEST READY: $($translation.Code)"
            Write-Host "Model: $($translation.Plan.model)"
            Write-Host "Request ID: $($translation.Plan.request_id)"
            Write-Host (
                'Maximum translation cost: JPY {0:N0}' -f
                $translation.Cost.translation.maximum_stage_cost_jpy
            )
            if (
                Test-TranslationCooldownRequired `
                    -AnalysisPreparation $preparation `
                    -TranslationPreparation $translation
            ) {
                Wait-ForTranslationCooldown `
                    -AnalysisCompletedAt $previousAnalysisCompletedAt `
                    -EstimatedTokenLoad (
                        Get-SameCredentialTokenLoad `
                            -AnalysisPreparation $preparation `
                            -TranslationPreparation $translation
                    )
            }
            $translationSucceeded = Invoke-LiveStage `
                -Code $translation.Code `
                -Stage translation `
                -RequestId $translation.Plan.request_id
            if (-not $translationSucceeded) {
                Write-Host (
                    "BATCH STATE: TRANSLATION FAILED for $($translation.Code)"
                )
                exit 4
            }
        }
    }

    Write-Host ''
    Write-Host 'BATCH STATE: SUCCESS'
    Write-Host "Experimental reports completed for: $($codes -join ', ')"
    exit 0
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
