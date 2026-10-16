@echo off
title ServDock - Linux & Docker Manager
echo Starting ServDock...
python "%~dp0main.py"
if %errorlevel% neq 0 (
    echo.
    echo Application exited with an error. Press any key to close.
    pause >nul
)
