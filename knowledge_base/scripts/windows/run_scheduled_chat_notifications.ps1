#requires -Version 5.1
<#
.SYNOPSIS
Runs scheduled Chat polling; delivery requires an explicit -Send.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('development', 'production')]
    [string]$Environment,
    [string]$ProjectRoot = '',
    [ValidateRange(1, 100)]
    [int]$MaxCount = 20,
    [ValidateRange(10, 3600)]
    [int]$TimeoutSeconds = 240,
    [switch]$Send
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$exitCode = 1
$process = $null
$started = $false
$lock = $null
$logFile = $null
$stage = 'preflight'
$mode = if ($Send) { 'send' } else { 'dry-run' }
$clock = [Diagnostics.Stopwatch]::StartNew()

function Write-SafeStatus([string]$EventName, [int]$Code) {
    # Only caller-controlled enum values and numbers; never exceptions or child output.
    $line = '{0} environment={1} mode={2} event={3} exit={4} elapsed_ms={5}' -f `
        [DateTime]::UtcNow.ToString('o'), $Environment, $mode, $EventName, $Code, $clock.ElapsedMilliseconds
    if ($null -ne $logFile) {
        Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
    }
    Write-Output $line
}

function Stop-ChildTree {
    if ($started -and $null -ne $process -and -not $process.HasExited) {
        # Windows PowerShell 5.1 has no Process.Kill(entireProcessTree) overload.
        $killer = Join-Path $env:SystemRoot 'System32\taskkill.exe'
        & $killer /PID $process.Id /T /F *> $null
        if (-not $process.WaitForExit(10000)) {
            throw 'Child termination did not complete.'
        }
    }
}

try {
    if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
        $ProjectRoot = Join-Path $PSScriptRoot '..\..'
    }
    $ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).ProviderPath
    $pythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $managePy = Join-Path $ProjectRoot 'manage.py'
    if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf) -or
        -not (Test-Path -LiteralPath $managePy -PathType Leaf)) {
        throw 'Required runtime files are missing.'
    }
    $logDir = Join-Path $ProjectRoot 'logs\scheduled_chat'
    [IO.Directory]::CreateDirectory($logDir) | Out-Null
    # One lock per deployment, also covering manual runs and differently named tasks.
    # Keep the file; deleting it on release would introduce a lock acquisition race.
    $stage = 'lock'
    try {
        $lock = [IO.File]::Open((Join-Path $logDir 'runner.lock'),
            [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    }
    catch [IO.IOException] {
        $exitCode = 75
        throw 'Lock is held or unavailable.'
    }
    $logFile = Join-Path $logDir ('runner-{0}.log' -f [DateTime]::UtcNow.ToString('yyyyMMdd'))
    $stage = 'launch'
    Write-SafeStatus 'start' 0
    $commandMode = if ($Send) { '--send' } else { '--dry-run' }
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $pythonExe
    $info.WorkingDirectory = $ProjectRoot
    # Fixed basename + validated enums/integers only: no shell or command injection.
    # --environment is checked by Django against DEBUG; it does not switch settings.
    $info.Arguments = 'manage.py send_scheduled_chat_notifications --environment {0} {1} --max-count {2} --no-color' -f `
        $Environment, $commandMode, $MaxCount
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    $started = $process.Start()
    if (-not $started) { throw 'Child did not start.' }
    # Drain both pipes concurrently into the null stream (bounded memory, no temp files).
    # Even plain dry-run prints message text; neither pipe is ever copied to logs.
    $stdoutDrain = $process.StandardOutput.BaseStream.CopyToAsync([IO.Stream]::Null)
    $stderrDrain = $process.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
    $stage = 'command'
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        $exitCode = 124
        $stage = 'timeout'
        Stop-ChildTree
    }
    else {
        $exitCode = $process.ExitCode
    }
    if (-not [Threading.Tasks.Task]::WaitAll(
        [Threading.Tasks.Task[]]@($stdoutDrain, $stderrDrain), 5000)) {
        throw 'Output draining did not complete.'
    }
    Write-SafeStatus 'finish' $exitCode
}
catch {
    if ($exitCode -eq 0) { $exitCode = 1 }
    try { Stop-ChildTree } catch { $exitCode = 125 }
    try { Write-SafeStatus ($stage + '-error') $exitCode }
    catch { [Console]::Error.WriteLine('Scheduled Chat runner failed; status logging unavailable.') }
}
finally {
    if ($null -ne $process) { $process.Dispose() }
    if ($null -ne $lock) { $lock.Dispose() }
}
exit $exitCode