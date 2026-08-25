@echo off
title TAI Tunnel - One Time Login
cd /d "%~dp0"
color 2F
echo ================================================
echo   ONE-TIME LOGIN (card ki zaroorat NAHI!)
echo ================================================
echo.
echo   Abhi ek BROWSER WINDOW khulegi:
echo.
echo   1. Wahan apna domain dikhega: code99.pk
echo   2. USI PE CLICK karo
echo   3. Phir "Authorize" button dabao
echo   4. Bas! Ye window khud band ho jayegi
echo.
echo ================================================
pause
echo.
cloudflared.exe tunnel login
echo.
if exist "%USERPROFILE%\.cloudflared\cert.pem" (
    color 2F
    echo [SUCCESS] Login ho gaya! Ab ye window band karo.
) else (
    color 4F
    echo [FAIL] Login complete nahi hua - dobara try karo.
)
pause
