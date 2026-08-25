[CmdletBinding()]
param(
    [int]$WarmupSeconds = 6,
    [int]$EdgeTtlSeconds = 5
)

$ErrorActionPreference = 'Stop'
$script:ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $script:ProjectRoot
New-Item -ItemType Directory -Force -Path run, reports | Out-Null

$pythonCommand = (Get-Command python -ErrorAction Stop).Source
$script:PythonExe = (& $pythonCommand -c 'import sys; print(sys.executable)').Trim()
if (-not (Test-Path $script:PythonExe)) { throw "Cannot resolve Python interpreter from $pythonCommand" }

function Start-Uvicorn {
    param([string[]]$Arguments, [hashtable]$ChildEnvironment, [string]$LogBase)

    # ProcessStartInfo avoids the Start-Process PATH/PATH collision seen in
    # some Windows shells while preserving the caller's Python environment.
    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $script:PythonExe
    $info.Arguments = ($Arguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' }) -join ' '
    $info.WorkingDirectory = $script:ProjectRoot
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $false
    $info.RedirectStandardError = $false
    $info.CreateNoWindow = $true
    $previousEnvironment = @{}
    foreach ($entry in $ChildEnvironment.GetEnumerator()) {
        $previousEnvironment[$entry.Key] = [Environment]::GetEnvironmentVariable($entry.Key, 'Process')
        Set-Item -Path "Env:$($entry.Key)" -Value $entry.Value
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $info
    try {
        [void]$process.Start()
    } finally {
        foreach ($entry in $previousEnvironment.GetEnumerator()) {
            if ($null -eq $entry.Value) { Remove-Item -Path "Env:$($entry.Key)" -ErrorAction SilentlyContinue }
            else { Set-Item -Path "Env:$($entry.Key)" -Value $entry.Value }
        }
    }
    return $process
}

function Start-Region {
    param([ValidateSet('a', 'b')][string]$Region, [int]$Port)

    $process = Start-Uvicorn -Arguments @(
        '-m', 'uvicorn', 'serving.app:app', '--host', '127.0.0.1', '--port', "$Port", '--log-level', 'warning'
    ) -ChildEnvironment @{ REGION = $Region; STATE_DIR = "state/region-$Region"; WARMUP_SECONDS = "$WarmupSeconds" } -LogBase "region-$Region"
    Set-Content -NoNewline -Path "run/region-$Region.pid" -Value $process.Id
    Write-Host "region-$Region pid=$($process.Id) port=$Port"
}

function Test-Service {
    param([int]$Port, [string]$Path)
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 -Uri "http://127.0.0.1:$Port$Path" | Out-Null
        return $true
    } catch {
        return $false
    }
}

Start-Region -Region a -Port 8001
Start-Region -Region b -Port 8002

$edge = Start-Uvicorn -Arguments @(
    '-m', 'uvicorn', 'edge.proxy:app', '--host', '127.0.0.1', '--port', '8080', '--log-level', 'warning'
) -ChildEnvironment @{ EDGE_TTL_SECONDS = "$EdgeTtlSeconds" } -LogBase 'edge'
Set-Content -NoNewline -Path 'run/edge.pid' -Value $edge.Id
Write-Host "edge pid=$($edge.Id) port=8080"

$services = @(
    @{ Name = 'region-a'; Port = 8001; Path = '/healthz' },
    @{ Name = 'region-b'; Port = 8002; Path = '/healthz' },
    @{ Name = 'edge';     Port = 8080; Path = '/edge/state' }
)

$allUp = $true
foreach ($service in $services) {
    $up = $false
    1..10 | ForEach-Object {
        if (-not $up -and (Test-Service -Port $service.Port -Path $service.Path)) { $up = $true }
        if (-not $up) { Start-Sleep -Seconds 1 }
    }
    if ($up) {
        Write-Host "  $($service.Name) (port $($service.Port)): UP"
    } else {
        Write-Error "$($service.Name) did not respond; inspect run/$($service.Name).log and run/$($service.Name).err.log"
        $allUp = $false
    }
}

if (-not $allUp) { exit 1 }

# The Microsoft Store Python launcher may hand work to a different python.exe.
# Save the PID that actually owns the listening port, so chaos/stop can target it.
foreach ($service in $services) {
    $listener = Get-NetTCPConnection -LocalPort $service.Port -State Listen -ErrorAction Stop |
        Select-Object -First 1 -ExpandProperty OwningProcess
    Set-Content -NoNewline -Path "run/$($service.Name).pid" -Value $listener
}
Invoke-RestMethod -Uri 'http://127.0.0.1:8080/edge/state'
