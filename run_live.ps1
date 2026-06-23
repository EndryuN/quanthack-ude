<#
.SYNOPSIS
    Launch the Quanthack trading engine.

.DESCRIPTION
    Runs the bot's main loop. DRY-RUN by default (computes and logs intended
    orders but sends nothing). Pass -Live to actually place orders.

    Output is shown on the console AND written to live_engine.log in real time.
    Press Ctrl+C to stop the engine cleanly.

.EXAMPLE
    .\run_live.ps1
    Dry-run: see what it would do, no orders sent.

.EXAMPLE
    .\run_live.ps1 -Live
    LIVE TRADING: sends real orders. Requires MetaTrader 5 running with Algo
    Trading enabled, and ANTHROPIC_API_KEY set in .env.
#>
param(
    [switch]$Live
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Build python args (-u = unbuffered, so the log updates in real time)
$pyArgs = @("-u", "-m", "bot.engine")
if ($Live) { $pyArgs += "--live" }

if ($Live) {
    Write-Host ""
    Write-Host "  *** LIVE TRADING *** — real orders WILL be sent." -ForegroundColor Red
    Write-Host "  Make sure MetaTrader 5 is running with Algo Trading enabled." -ForegroundColor Red
    Write-Host "  Press Ctrl+C to stop." -ForegroundColor Red
    Write-Host ""
} else {
    Write-Host ""
    Write-Host "  DRY-RUN — no orders will be sent. Pass -Live to trade." -ForegroundColor Yellow
    Write-Host ""
}

# Run, mirroring output to console and to live_engine.log
python @pyArgs 2>&1 | Tee-Object -FilePath "live_engine.log"
