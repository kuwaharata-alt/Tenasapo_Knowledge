param(
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8443
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\")).Path
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$managePy = Join-Path $projectRoot "manage.py"
$logDir = Join-Path $projectRoot "logs\django"

if (-not (Test-Path $pythonExe)) {
    throw "Python executable not found: $pythonExe"
}

if (-not (Test-Path $managePy)) {
    throw "manage.py not found: $managePy"
}

if (-not (Test-Path $logDir)) {
    New-Item -Path $logDir -ItemType Directory -Force | Out-Null
}

$bindAddress = "$HostAddress`:$Port"
$commandToken = "manage.py runsslserver $bindAddress"
$alreadyRunning = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and $_.CommandLine -like "*$commandToken*"
}

if ($alreadyRunning) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$timestamp] Stopping existing process(es) for restart..." | Add-Content -Path (Join-Path $logDir "django_startup.log") -Encoding UTF8
    $alreadyRunning | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "[$timestamp] Existing process(es) stopped. Starting new process..." | Add-Content -Path (Join-Path $logDir "django_startup.log") -Encoding UTF8
}

$stdoutLog = Join-Path $logDir "django_stdout.log"
$stderrLog = Join-Path $logDir "django_stderr.log"
$startupLog = Join-Path $logDir "django_startup.log"

$process = Start-Process `
    -FilePath $pythonExe `
    -ArgumentList @("manage.py", "runsslserver", $bindAddress) `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$timestamp] Startup success. PID=$($process.Id), Command=$commandToken" | Add-Content -Path $startupLog -Encoding UTF8
