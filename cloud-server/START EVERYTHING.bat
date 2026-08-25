@echo off
title TAI Cloud - Sab Kuch Chalu Karo
color 2F
echo ================================================
echo   TAI CLOUD - ONE CLICK START!
echo   2 windows khulengi (Server + Tunnel)
echo   Dono ko band MAT karna jab tak kaam hai!
echo ================================================
timeout /t 3 >nul
start "" "%~dp0RUN SERVER.bat"
timeout /t 2 >nul
start "" "%~dp0tunnel\START TUNNEL.bat"
echo.
echo   Done! Dono windows khul gayi - inko minimize kar do.
pause
