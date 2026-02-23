@echo off
chcp 65001 >nul
title Kimi Proxy - Port 8112

cd /d "%~dp0"

echo ========================================
echo   Kimi Proxy 启动中...
echo ========================================
echo.

:: 先激活环境
call conda activate base

:: 直接运行 python（此时输出会正常显示）
python -u kimi_proxy.py

pause