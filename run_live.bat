@echo off
REM Quanthack engine launcher (double-click friendly).
REM   run_live.bat          -> DRY-RUN (no orders sent)
REM   run_live.bat --live   -> LIVE TRADING (sends real orders)
cd /d "%~dp0"
python -u -m bot.engine %*
pause
