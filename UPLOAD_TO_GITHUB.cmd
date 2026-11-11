@echo off
setlocal EnableExtensions
title Quant Desk - Upload to GitHub

rem Change this only if your GitHub repository URL is different.
set "REMOTE_URL=https://github.com/lamvu0888-lab/quant-desk-v2.git"
set "BRANCH=main"

cd /d "%~dp0"
echo.
echo ================================================
echo        Quant Desk - GitHub Upload
echo ================================================
echo Folder: %CD%
echo.

where git >nul 2>&1
if errorlevel 1 (
    echo ERROR: Git is not installed or is not in PATH.
    echo Install Git for Windows, then run this file again.
    pause
    exit /b 1
)

if not exist ".git" (
    echo Initializing Git repository...
    git init
    git branch -M %BRANCH%
)

git remote get-url origin >nul 2>&1
if errorlevel 1 (
    git remote add origin "%REMOTE_URL%"
) else (
    git remote set-url origin "%REMOTE_URL%"
)

echo.
echo Checking Git identity...
for /f "delims=" %%A in ('git config user.email 2^>nul') do set "GIT_EMAIL=%%A"
for /f "delims=" %%A in ('git config user.name 2^>nul') do set "GIT_NAME=%%A"

if not defined GIT_EMAIL (
    set /p "GIT_EMAIL=Enter your GitHub email [lam.vu0888@gmail.com]: "
    if not defined GIT_EMAIL set "GIT_EMAIL=lam.vu0888@gmail.com"
    git config user.email "%GIT_EMAIL%"
)
if not defined GIT_NAME (
    set /p "GIT_NAME=Enter your name [Vu Lam]: "
    if not defined GIT_NAME set "GIT_NAME=Vu Lam"
    git config user.name "%GIT_NAME%"
)

echo.
echo Staging files...
git add -A
if errorlevel 1 goto :failed

git diff --cached --quiet
if not errorlevel 1 (
    echo No new changes to upload.
    goto :sync
)

set "COMMIT_MESSAGE=Update Quant Desk"
set /p "COMMIT_MESSAGE=Commit message [Update Quant Desk]: "
if not defined COMMIT_MESSAGE set "COMMIT_MESSAGE=Update Quant Desk"
git commit -m "%COMMIT_MESSAGE%"
if errorlevel 1 goto :failed

:sync
echo.
echo Downloading the latest GitHub history...
git fetch origin %BRANCH%
if errorlevel 1 (
    echo WARNING: Could not fetch from GitHub. Check your internet connection or login.
    goto :failed
)

git rev-parse --verify "origin/%BRANCH%" >nul 2>&1
if not errorlevel 1 (
    git merge-base --is-ancestor "origin/%BRANCH%" HEAD
    if errorlevel 1 (
        echo Merging remote changes...
        git pull --rebase origin %BRANCH%
        if errorlevel 1 (
            echo.
            echo Upload stopped because Git found a conflict.
            echo Resolve the conflict files, then run this file again.
            goto :failed
        )
    )
)

echo.
echo Uploading to GitHub...
git push -u origin %BRANCH%
if errorlevel 1 goto :failed

echo.
echo SUCCESS: Quant Desk was uploaded to GitHub.
echo Streamlit will redeploy automatically if it is connected to this repository.
echo.
pause
exit /b 0

:failed
echo.
echo Upload failed. No files were deleted.
echo Review the message above, fix the issue, and run this file again.
pause
exit /b 1
