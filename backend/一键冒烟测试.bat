@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
python scripts\smoke_doc_api.py
echo.
pause
