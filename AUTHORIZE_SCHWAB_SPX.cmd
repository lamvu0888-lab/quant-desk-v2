@echo off
setlocal
cd /d "%~dp0"
py -3 launch.py --schwab-authorize
pause
