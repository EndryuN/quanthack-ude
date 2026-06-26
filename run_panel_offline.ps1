<#
.SYNOPSIS
    Launch the SIGNAL//LAYER panel in OFFLINE / replay mode (no MetaTrader 5).

.DESCRIPTION
    Renders the ENTIRE dashboard from logs/ + state/ only — equity curve, decision
    history, the AI refresh log, consensus latest + the over-time heatmaps, and
    model scores. The live positions table and the instrument cone chart show their
    "live unavailable" placeholders (no MT5 / broker needed). Use this for a demo,
    or to review a frozen data snapshot — it never spawns the MT5 terminal.

    To replay a SNAPSHOT instead of the live logs/state, uncomment the two env vars
    and point them at a copied folder (e.g. demo_data\logs, demo_data\state).

.EXAMPLE
    .\run_panel_offline.ps1
    Open http://127.0.0.1:8800 — everything log-fed renders, no MT5.
#>
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$env:PANEL_NO_LIVE = "1"          # no MT5 at all; render from files only
# --- optional: replay a frozen snapshot instead of the live logs/state ---
# $env:PANEL_LOG_DIR   = "$PSScriptRoot\demo_data\logs"
# $env:PANEL_STATE_DIR = "$PSScriptRoot\demo_data\state"

Write-Host ""
Write-Host "  SIGNAL//LAYER panel - OFFLINE (no MT5)  ->  http://127.0.0.1:8800" -ForegroundColor Cyan
Write-Host "  Renders from logs/ + state/. Positions + instrument chart show 'unavailable'." -ForegroundColor Yellow
Write-Host ""

python -u -m uvicorn panel.backend.app:app --host 127.0.0.1 --port 8800
