@echo off
title StroboSync
cd /d "%~dp0"
python strobosync.py
if %errorlevel% neq 0 (
    echo.
    echo  [ERROR] Ejecuta install.bat primero.
    pause
)
