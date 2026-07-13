@echo off
echo ========================================
echo  Setup: Su Phu Chay Mau Predictor
echo ========================================

python --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Python chua duoc cai! Tai tai: https://python.org
    pause
    exit /b 1
)

echo [1/2] Cai cac thu vien can thiet...
pip install python-telegram-bot==21.3 streamlit pandas python-dotenv pillow matplotlib plotly numpy libsql==0.1.11 tzdata

echo.
echo [2/2] Tao file cau hinh...
if not exist .env (
    copy .env.example .env
    echo [OK] Da tao file .env
    echo >> Hay mo file .env va dien TELEGRAM_TOKEN cua ban!
) else (
    echo [OK] File .env da ton tai
)

echo.
echo ========================================
echo  Setup hoan tat!
echo.
echo  Buoc tiep theo:
echo  1. Mo file .env, dien TELEGRAM_TOKEN
echo  2. Chay: python main.py
echo     hoac chi web: python main.py --web
echo     hoac chi bot: python main.py --bot
echo ========================================
pause
