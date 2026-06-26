<#
.SYNOPSIS
    Launch the Quanthack trading engine (interactive) — PowerShell twin of run_live.bat.

.DESCRIPTION
    Asks two questions, numbered so option 1 is the first choice:
        Mode  -  [1] Live trading   [2] Dry-run
        Panel -  [1] With panel     [2] No panel
    e.g. live trading with panel = "1" then "1".

    With the panel ON, the operator dashboard launches in its own window at
    http://127.0.0.1:8800 and engine steering is enabled (QUANTHACK_PANEL=1).
    Engine output mirrors to the console AND live_engine.log. Ctrl+C stops cleanly.

    -Live skips the mode prompt (goes straight to live trading).

.EXAMPLE
    .\run_live.ps1
    Interactive: pick mode + panel.

.EXAMPLE
    .\run_live.ps1 -Live
    Skip the mode prompt; still asks about the panel.
#>
param(
    [switch]$Live
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "            QUANTHACK  LAUNCHER" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host ""

# --- Q1: Live trading or Dry-run?  (default = Dry-run) ---
if (-not $Live) {
    $m = Read-Host "Mode  -  [1] Live trading   [2] Dry-run (default 2)"
    if ($m -eq "1") { $Live = $true }
}

# --- Q2: With or without the operator panel?  (default = With panel) ---
$p = Read-Host "Panel -  [1] With panel   [2] No panel (default 1)"
$Panel = ($p -ne "2")

# Build python args (-u = unbuffered, so the log updates in real time)
$pyArgs = @("-u", "-m", "bot.engine")
if ($Live) { $pyArgs += "--live" }

Write-Host ""
if ($Live) {
    Write-Host "  Mode : *** LIVE TRADING - real orders WILL be sent ***" -ForegroundColor Red
} else {
    Write-Host "  Mode : DRY-RUN (no orders sent)" -ForegroundColor Yellow
}
if ($Panel) {
    Write-Host "  Panel: ON  -  dashboard at http://127.0.0.1:8800" -ForegroundColor Green
} else {
    Write-Host "  Panel: OFF"
}
Write-Host ""

# --- launch the operator panel in its own window + enable engine steering ---
if ($Panel) {
    $env:QUANTHACK_PANEL = "1"
    Start-Process powershell -WorkingDirectory $PSScriptRoot -ArgumentList @(
        "-NoExit", "-Command",
        "python -u -m uvicorn panel.backend.app:app --host 127.0.0.1 --port 8800")
}

# --- run the engine in THIS window (Ctrl+C to stop), mirroring to live_engine.log ---
python @pyArgs 2>&1 | Tee-Object -FilePath "live_engine.log"
