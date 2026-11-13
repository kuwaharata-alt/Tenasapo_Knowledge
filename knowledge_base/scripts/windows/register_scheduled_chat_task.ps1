#requires -Version 5.1
<#
.SYNOPSIS
Registers a current-user polling task, dry-run unless -Send is supplied.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('development', 'production')]
    [string]$Environment,
    [string]$ProjectRoot = '',
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')]
    [string]$TaskName = 'KB-Scheduled-Google-Chat',
    [ValidateRange(1, 1440)]
    [int]$IntervalMinutes = 5,
    [ValidateRange(1, 100)]
    [int]$MaxCount = 20,
    [ValidateRange(10, 3600)]
    [int]$TimeoutSeconds = 240,
    [switch]$Send
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
    if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
        $ProjectRoot = Join-Path $PSScriptRoot '..\..'
    }
    $ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).ProviderPath
    $runner = Join-Path $PSScriptRoot 'run_scheduled_chat_notifications.ps1'
    $runner = (Resolve-Path -LiteralPath $runner).ProviderPath
    $powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    foreach ($path in @($powershellExe, (Join-Path $ProjectRoot '.venv\Scripts\python.exe'),
                        (Join-Path $ProjectRoot 'manage.py'))) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Runtime file missing.' }
    }
    # Quoted -File arguments must not end with a backslash or contain quotes/newlines.
    $ProjectRoot = $ProjectRoot.TrimEnd('\')
    if ($ProjectRoot -match '["\r\n]' -or $runner -match '["\r\n]') { throw 'Unsupported path.' }
    $mode = if ($Send) { 'send' } else { 'dry-run' }
    $arguments = '-NoLogo -NoProfile -NonInteractive -File "{0}" -ProjectRoot "{1}" -Environment {2} -MaxCount {3} -TimeoutSeconds {4}' -f `
        $runner, $ProjectRoot, $Environment, $MaxCount, $TimeoutSeconds
    if ($Send) { $arguments += ' -Send' }

    # WhatIf never imports ScheduledTasks, starts Django, or registers anything.
    if ($PSCmdlet.ShouldProcess($TaskName, "Register $Environment $mode polling task every $IntervalMinutes minutes")) {
        Import-Module ScheduledTasks -ErrorAction Stop
        $existing = @(Get-ScheduledTask -TaskPath '\' -ErrorAction Stop |
            Where-Object { $_.TaskName -eq $TaskName })
        if ($existing.Count -gt 0) { throw 'Task already exists; no implicit replacement.' }
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
        $action = New-ScheduledTaskAction -Execute $powershellExe -Argument $arguments -WorkingDirectory $ProjectRoot
        # No duration = indefinite repetition; first poll is one minute after registration.
        $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
            -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
            -ExecutionTimeLimit (New-TimeSpan -Seconds ($TimeoutSeconds + 30)) `
            -RestartCount 0 -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        $task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
            -Description "Scheduled Chat polling ($Environment, $mode); no automatic retry."
        # Deliberately no -Force, password, elevation, or immediate Start-ScheduledTask.
        Register-ScheduledTask -TaskName $TaskName -TaskPath '\' -InputObject $task -ErrorAction Stop | Out-Null
        Write-Output "Registered: environment=$Environment mode=$mode interval_minutes=$IntervalMinutes"
    }
    exit 0
}
catch {
    # Do not echo arbitrary exception messages, paths, command output or environment.
    [Console]::Error.WriteLine('Scheduled Chat task registration failed. Check runtime files, task name collision, and current-user task permissions.')
    exit 1
}