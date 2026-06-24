@echo off
setlocal
REM Quanthack engine launcher - interactive (double-click friendly).
REM Asks two questions: (1) Live or Dry-run, (2) With or without the operator panel.
cd /d "%~dp0"

echo(
echo ===============================================
echo            QUANTHACK  LAUNCHER
echo ===============================================
echo(

REM --- Q1: Live trading or Dry-run? ---
set "LIVEARG="
set "MODE=DRY-RUN (no orders sent)"
choice /c LD /n /m "Mode  -  [L]ive trading   [D]ry-run : "
if errorlevel 2 (
    set "MODE=DRY-RUN (no orders sent)"
) else (
    set "LIVEARG=--live"
    set "MODE=*** LIVE TRADING - real orders WILL be sent ***"
)

REM --- Q2: With or without the operator panel? ---
set "PANEL=0"
choice /c WN /n /m "Panel -  [W]ith panel   [N]o panel : "
if errorlevel 2 (
    set "PANEL=0"
) else (
    set "PANEL=1"
)

echo(
echo   Mode : %MODE%
if "%PANEL%"=="1" (echo   Panel: ON  -  dashboard at http://127.0.0.1:8800) else (echo   Panel: OFF)
echo(

REM --- launch the operator panel in its own window + enable engine steering ---
if "%PANEL%"=="1" (
    set "QUANTHACK_PANEL=1"
    start "SIGNAL//LAYER panel" cmd /k python -u -m uvicorn panel.backend.app:app --host 127.0.0.1 --port 8800
)

REM --- run the engine in THIS window (Ctrl+C to stop) ---
python -u -m bot.engine %LIVEARG%
pause
endlocal
