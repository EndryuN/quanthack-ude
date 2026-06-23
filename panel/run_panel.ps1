# Launch the read-only control panel dashboard on http://127.0.0.1:8800
# Reads logs/ + state/ + live MT5 (read-only). Does NOT touch the trading engine.
$ErrorActionPreference = "Stop"
Set-Location -Path (Join-Path $PSScriptRoot "..")
python -m uvicorn panel.backend.app:app --host 127.0.0.1 --port 8800
