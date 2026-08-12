[CmdletBinding()]
param(
    [Parameter(
        Mandatory = $true,
        Position = 0,
        ValueFromRemainingArguments = $true
    )]
    [string[]]$SecurityCode,

    [switch]$PreviewOnly,
    [switch]$Key2Translation,
    [switch]$ProTranslation,
    [switch]$Pro,
    [switch]$Sol
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
$statusHelpers = Join-Path $PSScriptRoot 'api_status_helpers.ps1'
. $statusHelpers
$reportDate = Get-Date -Format 'yyyyMMdd'
$cooldownSeconds = 75
$cooldownTokenThreshold = 225000

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python virtual environment was not found at: $python"
}

function Resolve-Profile {
    $tokens = @($SecurityCode)
    $literalFlags = @{
        key2 = @($tokens | Where-Object { $_ -ieq '--key2-translation' }).Count
        proTranslation = @(
            $tokens | Where-Object { $_ -ieq '--pro-translation' }
        ).Count
        pro = @($tokens | Where-Object { $_ -ieq '--pro' }).Count
        sol = @($tokens | Where-Object { $_ -ieq '--sol' }).Count
    }
    foreach ($entry in $literalFlags.GetEnumerator()) {
        if ($entry.Value -gt 1) {
            throw "A model-profile option may be supplied only once."
        }
    }
    $selected = @(
        ($Key2Translation -or $literalFlags.key2 -eq 1),
        ($ProTranslation -or $literalFlags.proTranslation -eq 1),
        ($Pro -or $literalFlags.pro -eq 1),
        ($Sol -or $literalFlags.sol -eq 1)
    ).Where({ $_ }).Count
    if ($selected -gt 1) {
        throw (
            'Choose only one of --key2-translation, --pro-translation, ' +
            '--pro, or --sol.'
        )
    }
    if ($Sol -or $literalFlags.sol -eq 1) { return 'sol' }
    if ($Pro -or $literalFlags.pro -eq 1) { return 'pro' }
    if ($ProTranslation -or $literalFlags.proTranslation -eq 1) {
        return 'pro-translation'
    }
    if ($Key2Translation -or $literalFlags.key2 -eq 1) {
        return 'key2-translation'
    }
    return 'default'
}

function Resolve-SecurityCodes {
    $values = @(
        $SecurityCode |
            Where-Object {
                $_ -notin @(
                    '--key2-translation',
                    '--pro-translation',
                    '--pro',
                    '--sol'
                )
            }
    )
    $codes = @(
        foreach ($value in $values) {
            foreach ($part in ($value -split ',')) {
                $trimmed = $part.Trim()
                if ($trimmed) { $trimmed }
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
        $codes | Group-Object | Where-Object Count -gt 1
    )
    if ($duplicates.Count -gt 0) {
        throw (
            'Duplicate security codes are not allowed: ' +
            (($duplicates | ForEach-Object Name) -join ', ')
        )
    }
    return $codes
}

function Read-YesNo {
    param([Parameter(Mandatory)][string]$Prompt)
    while ($true) {
        $answer = (Read-Host "$Prompt (y/n)").Trim().ToLowerInvariant()
        if ($answer -eq 'y') { return $true }
        if ($answer -eq 'n') { return $false }
        Write-Host "Please enter 'y' or 'n'."
    }
}

function Invoke-OfflinePreparation {
    param(
        [Parameter(Mandatory)][string]$Code,
        [Parameter(Mandatory)]
        [ValidateSet('research', 'analysis', 'translation')]
        [string]$Stage,
        [Parameter(Mandatory)][string]$OutputRoot,
        [Parameter(Mandatory)][string]$ModelProfile
    )
    $env:TANSHIN_OFFLINE_ONLY = '1'
    $output = & $python -m tanshin_pipeline $Code `
        --repository-root $repositoryRoot `
        --output-root $OutputRoot `
        --report-date $reportDate `
        --stage $Stage `
        --model-profile $ModelProfile `
        --max-api-attempts 1 2>&1
    if ($LASTEXITCODE -ne 0) {
        $output | ForEach-Object { Write-Host $_ }
        throw (
            "Offline $Stage preparation failed for $Code. " +
            'No API request was sent.'
        )
    }
}

function Get-Preparation {
    param(
        [Parameter(Mandatory)][string]$Code,
        [Parameter(Mandatory)]
        [ValidateSet('research', 'analysis', 'translation')]
        [string]$Stage,
        [Parameter(Mandatory)][string]$OutputRoot,
        [Parameter(Mandatory)][string]$ModelProfile
    )
    Invoke-OfflinePreparation `
        -Code $Code `
        -Stage $Stage `
        -OutputRoot $OutputRoot `
        -ModelProfile $ModelProfile
    $artifacts = Join-Path $OutputRoot "$Code\artifacts"
    return [pscustomobject]@{
        Code = $Code
        Stage = $Stage
        Plan = (
            Get-Content -LiteralPath (
                Join-Path $artifacts "request_plan_$Stage.json"
            ) -Raw -Encoding UTF8 | ConvertFrom-Json
        )
        Cost = (
            Get-Content -LiteralPath (
                Join-Path $artifacts 'cost.json'
            ) -Raw -Encoding UTF8 | ConvertFrom-Json
        )
        Manifest = (
            Get-Content -LiteralPath (
                Join-Path $artifacts 'selection_manifest.json'
            ) -Raw -Encoding UTF8 | ConvertFrom-Json
        )
    }
}

function Get-StageBudget {
    param([Parameter(Mandatory)][object]$Preparation)
    $stageCost = $Preparation.Cost.($Preparation.Stage)
    return (
        [long]$stageCost.estimated_input_tokens +
        [long]$stageCost.maximum_output_tokens
    )
}

function Wait-BetweenStagesIfNeeded {
    param(
        [Parameter(Mandatory)][object]$Previous,
        [Parameter(Mandatory)][object]$Next,
        [Parameter(Mandatory)][datetime]$PreviousCompletedAt
    )
    if (
        $Previous.Plan.provider -ne 'gemini' -or
        $Next.Plan.provider -ne 'gemini' -or
        $Previous.Plan.provider_profile -ne $Next.Plan.provider_profile
    ) {
        Write-Host (
            'SAME-CREDENTIAL COOLDOWN: not needed; the consecutive stages ' +
            'use different providers or credentials.'
        )
        return
    }
    $combinedBudget = (
        (Get-StageBudget -Preparation $Previous) +
        (Get-StageBudget -Preparation $Next)
    )
    if ($combinedBudget -lt $cooldownTokenThreshold) {
        Write-Host (
            "SAME-CREDENTIAL COOLDOWN: not needed; estimated combined load " +
            "$combinedBudget tokens is below $cooldownTokenThreshold."
        )
        return
    }
    $elapsed = ((Get-Date) - $PreviousCompletedAt).TotalSeconds
    $remaining = [Math]::Ceiling(
        [Math]::Max(0, $cooldownSeconds - $elapsed)
    )
    if ($remaining -le 0) {
        Write-Host (
            "SAME-CREDENTIAL COOLDOWN: already satisfied " +
            "($cooldownSeconds seconds elapsed)."
        )
        return
    }
    Write-Host (
        "SAME-CREDENTIAL COOLDOWN: estimated combined load $combinedBudget " +
        "tokens meets the $cooldownTokenThreshold-token threshold; waiting " +
        "$remaining seconds."
    )
    Start-Sleep -Seconds $remaining
}

function Wait-BeforeNextCompany {
    param([Parameter(Mandatory)][datetime]$PreviousResearchCompletedAt)
    $elapsed = ((Get-Date) - $PreviousResearchCompletedAt).TotalSeconds
    $remaining = [Math]::Ceiling(
        [Math]::Max(0, $cooldownSeconds - $elapsed)
    )
    if ($remaining -gt 0) {
        Write-Host (
            "INTER-COMPANY COOLDOWN: waiting $remaining seconds before the " +
            'next PDF-backed research request.'
        )
        Start-Sleep -Seconds $remaining
    }
}

function Backup-CurrentOutput {
    param(
        [Parameter(Mandatory)][string]$Code,
        [Parameter(Mandatory)][string]$BatchStamp
    )
    $currentOutput = Join-Path $repositoryRoot "final_output\$Code"
    if (-not (Test-Path -LiteralPath $currentOutput -PathType Container)) {
        return
    }
    $items = @(
        Get-ChildItem -LiteralPath $currentOutput -Force |
            Where-Object Name -ne 'history'
    )
    if ($items.Count -eq 0) { return }
    $archive = Join-Path $currentOutput "history\$BatchStamp"
    New-Item -ItemType Directory -Path $archive -Force | Out-Null
    $items | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $archive -Recurse
    }
    $pattern = (
        '^analysis_(ja|en)_' + [regex]::Escape($Code) + '_\d{8}\.md$'
    )
    Get-ChildItem -LiteralPath $currentOutput -File |
        Where-Object Name -match $pattern |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName }
    Write-Host "Archived existing $Code output to: $archive"
}

function Invoke-LiveStage {
    param(
        [Parameter(Mandatory)][object]$Preparation,
        [Parameter(Mandatory)][string]$ModelProfile
    )
    $code = $Preparation.Code
    $stage = $Preparation.Stage
    $requestId = $Preparation.Plan.request_id
    Remove-Item Env:TANSHIN_OFFLINE_ONLY -ErrorAction SilentlyContinue
    $env:TANSHIN_LIVE_API = 'MANUAL_USER_RUN'
    Write-Host ''
    Write-Host ('=' * 72)
    Write-Host "COMPANY: $code"
    Write-Host "STAGE: $($stage.ToUpperInvariant())"
    Write-Host 'MODEL RUN STATE: RUNNING'
    & $python -m tanshin_pipeline $code `
        --repository-root $repositoryRoot `
        --report-date $reportDate `
        --stage $stage `
        --model-profile $ModelProfile `
        --execute-api `
        --confirm-request $requestId `
        --max-api-attempts 1
    $exitCode = $LASTEXITCODE
    $statusPath = Join-Path (
        $repositoryRoot
    ) "final_output\$code\artifacts\api_status_$stage.json"
    if (-not (Test-Path -LiteralPath $statusPath -PathType Leaf)) {
        Write-Host 'MODEL RUN STATE: UNKNOWN (no status artifact)'
        Write-Host 'REPORT PIPELINE STATE: NOT_COMPLETED'
        return $false
    }
    $status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 |
        ConvertFrom-Json
    Write-ApiStatusSummary `
        -ApiStatus $status `
        -ExpectedRunId $requestId `
        -ExecutionExitCode $exitCode
    $state = Get-OptionalJsonProperty -InputObject $status -Name 'state'
    if ($state -eq 'SUCCESS' -and $exitCode -eq 0) {
        return $true
    }
    if ($stage -eq 'research' -and $state -eq 'SUCCESS') {
        Write-Host (
            'RESEARCH RECOVERY: the provider response succeeded but local ' +
            'processing did not finish. Reprocessing the saved response ' +
            'offline and continuing to synthesis.'
        )
        $env:TANSHIN_OFFLINE_ONLY = '1'
        Remove-Item Env:TANSHIN_LIVE_API -ErrorAction SilentlyContinue
        & $python -m tanshin_pipeline $code `
            --repository-root $repositoryRoot `
            --report-date $reportDate `
            --stage research `
            --model-profile $ModelProfile `
            --reprocess-stored
        if ($LASTEXITCODE -eq 0) {
            Write-Host 'RESEARCH RECOVERY STATE: SUCCESS'
            return $true
        }
        Write-Host 'RESEARCH RECOVERY STATE: FAILED'
    }
    return $false
}

function Write-ResearchPreparation {
    param([Parameter(Mandatory)][object]$Preparation)
    $code = $Preparation.Code
    $plan = $Preparation.Plan
    $cost = $Preparation.Cost
    $manifest = $Preparation.Manifest
    Write-Host ''
    Write-Host ('=' * 72)
    Write-Host "COMPANY: $code"
    Write-Host "Latest filing: $($manifest.latest_filename)"
    Write-Host "Model profile: $($plan.model_profile)"
    Write-Host "Research provider/model: $($plan.provider) / $($plan.model)"
    Write-Host "Research request ID: $($plan.request_id)"
    Write-Host (
        'Estimated maximum research cost: JPY {0:N0}' -f
        $cost.research.maximum_stage_cost_jpy
    )
    Write-Host (
        'Estimated maximum synthesis cost: JPY {0:N0}' -f
        $cost.analysis.maximum_stage_cost_jpy
    )
    Write-Host (
        'Estimated maximum optional translation cost: JPY {0:N0}' -f
        $cost.translation.maximum_stage_cost_jpy
    )
    Write-Host "PDFs submitted in research request: $($plan.files.Count)"
    foreach ($file in $plan.files) {
        Write-Host "  - $($file.filename) ($($file.page_count) pages)"
    }
    Write-Host (
        "Expected Japanese report: final_output\$code\" +
        "analysis_ja_${code}_$reportDate.md"
    )
    Write-Host (
        "Optional English report: final_output\$code\" +
        "analysis_en_${code}_$reportDate.md"
    )
    Write-Host (
        'Japanese workflow: PDF research dossier, then dossier-based synthesis. '
    )
    Write-Host (
        'Research diagnostics: model_response_research.raw.json, ' +
        'research.structured.json, research_metrics.json, ' +
        'validation_research.json, api_status_research.json, token_usage.json, ' +
        'and cost.json'
    )
}

$codes = @(Resolve-SecurityCodes)
$modelProfile = Resolve-Profile
$oldOffline = $env:TANSHIN_OFFLINE_ONLY
$oldLive = $env:TANSHIN_LIVE_API
$preflightParent = Join-Path $repositoryRoot 'tmp'
$preflightRoot = Join-Path (
    $preflightParent
) ("batch_preflight_" + [guid]::NewGuid().ToString('N'))

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

    $researchPreparations = @(
        foreach ($code in $codes) {
            Get-Preparation `
                -Code $code `
                -Stage research `
                -OutputRoot $preflightRoot `
                -ModelProfile $modelProfile
        }
    )
    $researchPreparations | ForEach-Object {
        Write-ResearchPreparation -Preparation $_
    }

    $researchCost = (
        $researchPreparations |
            ForEach-Object Cost |
            ForEach-Object { $_.research.maximum_stage_cost_jpy } |
            Measure-Object -Sum
    ).Sum
    $analysisCost = (
        $researchPreparations |
            ForEach-Object Cost |
            ForEach-Object { $_.analysis.maximum_stage_cost_jpy } |
            Measure-Object -Sum
    ).Sum
    $translationCost = (
        $researchPreparations |
            ForEach-Object Cost |
            ForEach-Object { $_.translation.maximum_stage_cost_jpy } |
            Measure-Object -Sum
    ).Sum
    $pages = (
        $researchPreparations |
            ForEach-Object Manifest |
            ForEach-Object total_selected_pages |
            Measure-Object -Sum
    ).Sum

    Write-Host ''
    Write-Host ('=' * 72)
    Write-Host 'BATCH TOTALS'
    Write-Host "Companies: $($codes.Count)"
    Write-Host "Selected PDF pages: $pages"
    Write-Host (
        'Maximum Japanese report cost (research + synthesis): JPY {0:N0}' -f
        ($researchCost + $analysisCost)
    )
    Write-Host (
        'Maximum Japanese + English cost: JPY {0:N0}' -f
        ($researchCost + $analysisCost + $translationCost)
    )
    if ($modelProfile -eq 'default') {
        Write-Host (
            'Billing note: This profile uses only GEMINI_API_KEY and should ' +
            "be free when that key's project is eligible for the Gemini free " +
            'tier; the JPY estimate is a paid-tier upper bound.'
        )
    }
    Write-Host 'Each stage uses one API attempt; there are no automatic retries.'
    Write-Host (
        "Consecutive same-credential Gemini stages wait up to " +
        "$cooldownSeconds seconds when their combined estimated token budget " +
        "reaches $cooldownTokenThreshold."
    )

    if ($PreviewOnly) {
        Write-Host ''
        Write-Host 'PREVIEW ONLY: no API request was sent.'
        exit 0
    }

    if (-not (Read-YesNo -Prompt (
        "Proceed with two-stage Japanese analysis for all $($codes.Count) companies"
    ))) {
        Write-Host 'Cancelled. No API request was sent.'
        exit 0
    }
    $includeEnglish = Read-YesNo -Prompt (
        "Generate an English report immediately after each Japanese report"
    )
    $requestsPerCompany = if ($includeEnglish) { 3 } else { 2 }
    Write-Host (
        "Authorized batch: $($requestsPerCompany * $codes.Count) manually " +
        "initiated API requests across $($codes.Count) companies."
    )
    Write-Host (
        'Companies run sequentially: PDF research, Japanese synthesis, ' +
        'optional English translation, then the next company.'
    )
    if ($env:TANSHIN_TESTING -eq '1') {
        throw (
            'TANSHIN_TESTING=1 blocks live execution. Open a normal ' +
            'PowerShell session.'
        )
    }

    $batchStamp = Get-Date -Format 'yyyyMMdd_HHmmssfff'
    $previousResearchCompletedAt = $null
    foreach ($preflight in $researchPreparations) {
        if ($null -ne $previousResearchCompletedAt) {
            Wait-BeforeNextCompany `
                -PreviousResearchCompletedAt $previousResearchCompletedAt
        }
        Backup-CurrentOutput `
            -Code $preflight.Code `
            -BatchStamp $batchStamp

        $canonicalRoot = Join-Path $repositoryRoot 'final_output'
        $research = Get-Preparation `
            -Code $preflight.Code `
            -Stage research `
            -OutputRoot $canonicalRoot `
            -ModelProfile $modelProfile
        if (-not (Invoke-LiveStage -Preparation $research -ModelProfile $modelProfile)) {
            Write-Host "BATCH STATE: RESEARCH FAILED for $($research.Code)"
            exit 3
        }
        $researchCompletedAt = Get-Date
        $previousResearchCompletedAt = $researchCompletedAt

        Write-Host (
            "Research succeeded for $($research.Code). Preparing Japanese " +
            'synthesis offline from the stored dossier.'
        )
        $analysis = Get-Preparation `
            -Code $research.Code `
            -Stage analysis `
            -OutputRoot $canonicalRoot `
            -ModelProfile $modelProfile
        Wait-BetweenStagesIfNeeded `
            -Previous $research `
            -Next $analysis `
            -PreviousCompletedAt $researchCompletedAt
        if (-not (Invoke-LiveStage -Preparation $analysis -ModelProfile $modelProfile)) {
            Write-Host "BATCH STATE: ANALYSIS FAILED for $($analysis.Code)"
            exit 4
        }
        $analysisCompletedAt = Get-Date

        if ($includeEnglish) {
            Write-Host (
                "Japanese synthesis succeeded for $($analysis.Code). " +
                'Preparing translation offline.'
            )
            $translation = Get-Preparation `
                -Code $analysis.Code `
                -Stage translation `
                -OutputRoot $canonicalRoot `
                -ModelProfile $modelProfile
            Wait-BetweenStagesIfNeeded `
                -Previous $analysis `
                -Next $translation `
                -PreviousCompletedAt $analysisCompletedAt
            if (-not (
                Invoke-LiveStage `
                    -Preparation $translation `
                    -ModelProfile $modelProfile
            )) {
                Write-Host (
                    "BATCH STATE: TRANSLATION FAILED for $($translation.Code)"
                )
                exit 5
            }
        }
    }

    Write-Host ''
    if ($includeEnglish) {
        Write-Host 'BATCH STATE: SUCCESS'
        Write-Host (
            "Japanese and English reports completed for: $($codes -join ', ')"
        )
    } else {
        Write-Host 'BATCH STATE: SUCCESS (Japanese reports only)'
    }
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
    $resolvedParent = [IO.Path]::GetFullPath($preflightParent)
    $resolvedPreflight = [IO.Path]::GetFullPath($preflightRoot)
    if (
        (Test-Path -LiteralPath $resolvedPreflight -PathType Container) -and
        ([IO.Path]::GetDirectoryName($resolvedPreflight) -eq $resolvedParent)
    ) {
        Remove-Item -LiteralPath $resolvedPreflight -Recurse -Force
    }
}
