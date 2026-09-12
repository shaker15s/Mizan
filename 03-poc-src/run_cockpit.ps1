# Agent-Native ERP Daylight Web Cockpit Launcher
# Starts the zero-dependency web server and launches the browser cockpit.

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

$PythonExe = Join-Path $ScriptDir ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Host "Virtual environment python not found at $PythonExe. Using system python..." -ForegroundColor Yellow
    $PythonExe = "python"
}

$env:PYTHONPATH = $ScriptDir

Write-Host "`n=======================================================" -ForegroundColor Cyan
Write-Host " 🚀 Starting Agent-Native ERP Daylight Web Cockpit" -ForegroundColor Green
Write-Host " 🌐 Connecting to Odoo 19 & Audit Gateway" -ForegroundColor Cyan
Write-Host "=======================================================`n" -ForegroundColor Cyan

& $PythonExe -m poc.web_server --port 8080 --open
