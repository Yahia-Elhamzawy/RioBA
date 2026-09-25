@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Web QA Agent Server
echo =========================================
echo    Starting Web QA Agent Server
echo =========================================
echo.
echo Starting server... The dashboard will open in your browser automatically.
echo.

python server.py

echo.
echo Server stopped.
pause
