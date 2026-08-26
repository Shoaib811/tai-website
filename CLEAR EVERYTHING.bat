@echo off
title TAI — Clear Everything
color 0C
echo.
echo  ============================================
echo   TAI — CLEAR EVERYTHING
echo  ============================================
echo.
echo  Ye script TAI ko bilkul VIRGIN bana degi:
echo    - Saare cloud accounts delete
echo    - Saare parts delete
echo    - Saari mappings delete
echo    - Local config reset
echo    - App dubara se fresh start karega
echo.
echo  !!! WARNING: Ye SAB data delete kar dega !!!
echo  !!! Login phir se karna padega             !!!
echo.
set /p confirm="Really sab delete karna hai? (YES/NO): "
if /i not "%confirm%"=="YES" (
    echo Cancelled.
    pause
    exit /b
)
echo.
echo [1/4] Deleting cloud database...
cd /d "%~dp0cloud-server"
if exist data\taicloud.db (
    del /f /q data\taicloud.db
    echo   Deleted: taicloud.db
) else (
    echo   taicloud.db not found — skipping
)
echo.
echo [2/4] Deleting all cloud parts...
if exist data\parts (
    rmdir /s /q data\parts
    echo   Deleted: data\parts\
) else (
    echo   data\parts not found — skipping
)
echo.
echo [3/4] Clearing local config...
cd /d "%~dp0tai"
if exist tai_cloud_config.json (
    echo {"server":"https://api.code99.pk/api/v1"} > tai_cloud_config.json
    echo   Reset: tai_cloud_config.json
) else (
    echo   tai_cloud_config.json not found — skipping
)
if exist tai_config.json (
    del /f /q tai_config.json
    echo   Deleted: tai_config.json
) else (
    echo   tai_config.json not found — skipping
)
echo.
echo [4/4] Cleaning TAI folders, desktop icon and temp files...
powershell -NoProfile -Command "Remove-Item -LiteralPath ([Environment]::GetFolderPath('Desktop') + '\TAI - Tricrypt AI.lnk') -Force -ErrorAction SilentlyContinue"
if not exist "%USERPROFILE%\OneDrive\Desktop\TAI - Tricrypt AI.lnk" (
    echo   Deleted: Desktop icon
) else (
    echo   Desktop icon not found — skipping
)
if exist "C:\TAI" (
    rmdir /s /q "C:\TAI"
    echo   Deleted: C:\TAI\
) else (
    echo   C:\TAI not found — skipping
)
if exist "%TEMP%\TAI" (
    rmdir /s /q "%TEMP%\TAI"
    echo   Deleted: %%TEMP%%\TAI\
) else (
    echo   Temp TAI folder not found — skipping
)
echo.
echo  ============================================
echo   DONE! TAI is now VIRGIN.
echo   Run TAI.bat to start fresh.
echo  ============================================
echo.
pause
