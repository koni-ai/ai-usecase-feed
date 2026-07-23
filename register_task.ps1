[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$TaskName = "AI 활용사례 피드 매일 업데이트",
    [ValidatePattern('^(?:[01]\d|2[0-3]):[0-5]\d$')]
    [string]$Time = "07:00"
)

$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runPath = Join-Path $projectDir "run.py"

if (-not (Test-Path -LiteralPath $runPath -PathType Leaf)) {
    throw "통합 실행기를 찾을 수 없습니다: $runPath"
}

$python = Get-Command python -ErrorAction Stop
$claude = Get-Command claude -ErrorAction Stop
$authJson = & $claude.Source auth status --json
if ($LASTEXITCODE -ne 0) {
    throw "Claude 로그인 상태를 확인하지 못했습니다."
}

$auth = $authJson | ConvertFrom-Json
$allowedSubscriptions = @("pro", "max", "team", "enterprise")
if (
    -not $auth.loggedIn -or
    $auth.authMethod -ne "claude.ai" -or
    $allowedSubscriptions -notcontains $auth.subscriptionType
) {
    throw "추가 과금 없는 Claude 구독 로그인이 필요합니다."
}

$action = New-ScheduledTaskAction `
    -Execute $python.Source `
    -Argument "-B `"$runPath`"" `
    -WorkingDirectory $projectDir
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

if ($PSCmdlet.ShouldProcess($TaskName, "매일 $Time 예약 작업 등록 또는 갱신")) {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Description "기존 Claude 구독으로 AI 활용사례를 수집·가공하고 정적 HTML을 갱신합니다." `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Force | Out-Null
    Write-Output "등록 완료: $TaskName / 매일 $Time / 로그인 중일 때 실행"
}
