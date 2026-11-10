@echo off
setlocal
cd /d "%~dp0"
title Quant Desk 2.1
py -3 launch.py
if errorlevel 1 (
  echo.
  echo Launch failed. Read the message above. Install Python 3.12 if Python was not found.
)
pause
