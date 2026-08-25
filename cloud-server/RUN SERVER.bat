@echo off
title TAI Cloud Server (localhost:8800)
cd /d "%~dp0"
echo ============================================
echo   TAI CLOUD SERVER - http://127.0.0.1:8800
echo   API docs: http://127.0.0.1:8800/docs
echo   Band karne ke liye: Ctrl+C
echo ============================================
"C:\Users\cccsh\AppData\Local\Programs\Python\Python314\python.exe" expire_plans.py
"C:\Users\cccsh\AppData\Local\Programs\Python\Python314\python.exe" app.py
pause
