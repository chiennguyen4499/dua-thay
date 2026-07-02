@echo off
chcp 65001 >nul
title Dua Thay - Web UI
cd /d "%~dp0"

echo ============================================
echo   DUA THAY - dang khoi dong Web UI...
echo   Trinh duyet se tu mo sau vai giay.
echo   De TAT: dong cua so nay hoac bam Ctrl+C.
echo ============================================
echo.

rem Mo trinh duyet sau 5 giay (cho server kip chay)
start "" /min cmd /c "timeout /t 5 >nul & start "" http://localhost:8501"

rem Chay web (giu o cua so nay de co the tat bang Ctrl+C)
python main.py --web

echo.
echo Web da dung. Bam phim bat ky de dong.
pause >nul
