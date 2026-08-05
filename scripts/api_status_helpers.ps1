function Get-OptionalJsonProperty {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object]$InputObject,

        [Parameter(Mandatory)]
        [string]$Name
    )

    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Write-ApiStatusSummary {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object]$ApiStatus,

        [Parameter(Mandatory)]
        [string]$ExpectedRunId,

        [Parameter(Mandatory)]
        [int]$ExecutionExitCode
    )

    $runId = Get-OptionalJsonProperty -InputObject $ApiStatus -Name 'run_id'
    if ($runId -ne $ExpectedRunId) {
        Write-Host 'MODEL RUN STATE: UNKNOWN (status run ID mismatch)'
        Write-Host 'REPORT PIPELINE STATE: NOT_COMPLETED'
        return
    }

    $state = Get-OptionalJsonProperty -InputObject $ApiStatus -Name 'state'
    if ([string]::IsNullOrWhiteSpace([string]$state)) {
        $state = 'UNKNOWN'
    }
    Write-Host "MODEL RUN STATE: $state"
    $provider = Get-OptionalJsonProperty -InputObject $ApiStatus -Name 'provider'
    if ($null -ne $provider) {
        Write-Host "Provider: $provider"
    }

    $responseId = Get-OptionalJsonProperty -InputObject $ApiStatus -Name 'response_id'
    if ($null -ne $responseId) {
        Write-Host "Response ID: $responseId"
    }
    $finishReason = Get-OptionalJsonProperty -InputObject $ApiStatus -Name 'finish_reason'
    if ($null -ne $finishReason) {
        Write-Host "Finish reason: $finishReason"
    }
    $attempts = Get-OptionalJsonProperty -InputObject $ApiStatus -Name 'attempts'
    if ($null -ne $attempts) {
        Write-Host "Attempts: $attempts"
    }

    if ($state -in @('RATE_LIMITED', 'TEMPORARILY_UNAVAILABLE', 'FAILED')) {
        $statusCode = Get-OptionalJsonProperty -InputObject $ApiStatus -Name 'status_code'
        $errorType = Get-OptionalJsonProperty -InputObject $ApiStatus -Name 'error_type'
        $errorSummary = Get-OptionalJsonProperty -InputObject $ApiStatus -Name 'error_summary'
        $retryGuidance = Get-OptionalJsonProperty -InputObject $ApiStatus -Name 'retry_guidance'
        if ($null -ne $statusCode) {
            Write-Host "Status code: $statusCode"
        }
        if ($null -ne $errorType) {
            Write-Host "Failure type: $errorType"
        }
        if ($null -ne $errorSummary) {
            Write-Host "Failure summary: $errorSummary"
        }
        if ($null -ne $retryGuidance) {
            Write-Host "Retry guidance: $retryGuidance"
        }
    }

    if ($state -eq 'SUCCESS' -and $ExecutionExitCode -eq 0) {
        Write-Host 'REPORT PIPELINE STATE: SUCCESS'
    } elseif ($state -eq 'SUCCESS') {
        Write-Host 'REPORT PIPELINE STATE: PROCESSING_FAILED_AFTER_API_SUCCESS'
    } else {
        Write-Host 'REPORT PIPELINE STATE: NOT_COMPLETED'
        Write-Host (
            'No report from this request was generated. Existing report files, if any, ' +
            'belong to an earlier run; verify their report_status run ID.'
        )
    }
}
