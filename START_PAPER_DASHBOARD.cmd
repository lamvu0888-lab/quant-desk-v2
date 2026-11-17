@echo off
setlocal
cd /d "%~dp0"
set "QD_PY=python"
if exist ".venv\Scripts\python.exe" set "QD_PY=.venv\Scripts\python.exe"
"%QD_PY%" -c "import streamlit, dotenv, exchange_calendars, scipy, plotly" >nul 2>&1
if errorlevel 1 (
  "%QD_PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
"%QD_PY%" -m streamlit run paper_dashboard.py --server.port 8502 --server.address 127.0.0.1
if errorlevel 1 pause
