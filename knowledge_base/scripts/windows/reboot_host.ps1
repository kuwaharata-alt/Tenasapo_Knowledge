param(
    [int]$DelaySeconds = 30,
    [string]$Reason = "KB periodic reboot"
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\")).Path
$logDir = Join-Path $projectRoot "logs\django"
if (-not (Test-Path $logDir)) {
    New-Item -Path $logDir -ItemType Directory -Force | Out-Null
}

$logFile = Join-Path $logDir "scheduled_reboot.log"
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$timestamp] Reboot requested. DelaySeconds=$DelaySeconds, Reason=$Reason" | Add-Content -Path $logFile -Encoding UTF8

shutdown.exe /r /t $DelaySeconds /f /d p:4:1 /c $Reason
