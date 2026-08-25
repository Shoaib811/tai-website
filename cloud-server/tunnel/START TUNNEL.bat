@echo off
title TAI Cloud Tunnel (api.code99.pk)
cd /d "%~dp0"
color 2F
echo ================================================
echo   TAI CLOUD TUNNEL CHALU!
echo   Public: https://api.code99.pk
echo   Local : http://localhost:8800
echo -----------------------------------------------
echo   NOTE: 'RUN SERVER.bat' bhi chalu hona chahiye!
echo   Band karne ke liye: ye window CLOSE karo
echo ================================================
echo.

cloudflared.exe tunnel run --url http://localhost:8800 tai-cloud
pause
