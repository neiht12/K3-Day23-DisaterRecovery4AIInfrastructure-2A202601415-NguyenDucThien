[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Get-ChildItem 'run' -Filter '*.pid' -ErrorAction SilentlyContinue | ForEach-Object {
    $pidText = Get-Content -Raw $_.FullName -ErrorAction SilentlyContinue
    $processId = 0
    if ([int]::TryParse($pidText.Trim(), [ref]$processId)) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $_.FullName -Force
}

Write-Host 'all stopped'
