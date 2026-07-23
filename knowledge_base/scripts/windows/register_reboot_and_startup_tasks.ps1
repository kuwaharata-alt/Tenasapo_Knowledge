param(
    [string]$RebootTime = "03:00",
    [string]$RebootDay = "SUN",
    [string]$RebootTaskName = "KB-Periodic-Reboot",
    [string]$StartupTaskName = "KB-Start-Django-After-Boot"
)

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
if (-not $isAdmin) {
    throw "管理者として PowerShell を実行してください。"
}

$scriptRoot = $PSScriptRoot
$rebootScript = Join-Path $scriptRoot "reboot_host.ps1"
$startupScript = Join-Path $scriptRoot "start_django_after_boot.ps1"
$psExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path $rebootScript)) {
    throw "Script not found: $rebootScript"
}

if (-not (Test-Path $startupScript)) {
    throw "Script not found: $startupScript"
}

$rebootArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$rebootScript`" -DelaySeconds 30 -Reason `"KB periodic reboot`""
$startupArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$startupScript`" -HostAddress `"0.0.0.0`" -Port 8443"

$rebootTr = "`"$psExe`" $rebootArgs"
$startupTr = "`"$psExe`" $startupArgs"

schtasks /Create /TN $RebootTaskName /SC WEEKLY /D $RebootDay /ST $RebootTime /TR $rebootTr /RL HIGHEST /F | Out-Null
schtasks /Create /TN $StartupTaskName /SC ONSTART /TR $startupTr /RL HIGHEST /F | Out-Null

Write-Output "Task registration completed."
Write-Output "- $RebootTaskName (WEEKLY $RebootDay $RebootTime)"
Write-Output "- $StartupTaskName (ONSTART)"
