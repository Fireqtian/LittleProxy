@echo off
chcp 65001 >nul
title Little Proxy - Port 8111

cd /d "%~dp0"

echo ========================================
echo   Little Proxy 启动中...
echo ========================================
echo.

conda activate base
python little_proxy.py

pause