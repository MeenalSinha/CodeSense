@echo off
title CodeSense Frontend
color 0B

echo.
echo  ==========================================
echo    CodeSense Frontend  ^|  React + Vite
echo  ==========================================
echo.

where node >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Node.js not found.
    echo  Install LTS version from: https://nodejs.org
    pause
    exit /b 1
)

echo  [OK] Node.js found.
echo.

if not exist "frontend\" (
    echo  [ERROR] Cannot find the frontend\ folder.
    echo  Run this script from the root CodeSense folder.
    pause
    exit /b 1
)

cd frontend

if not exist "node_modules\" (
    echo  [SETUP] Installing npm packages (first run takes 1-2 minutes)...
    echo.
    npm install
    if %errorlevel% neq 0 (
        echo  [ERROR] npm install failed.
        pause
        exit /b 1
    )
    echo.
    echo  [OK] Packages installed.
) else (
    echo  [OK] node_modules already exists. Skipping install.
)

echo.
echo  ==========================================
echo    Starting React dev server
echo    http://localhost:3000
echo  ==========================================
echo.
echo  Make sure the backend is running first!
echo  Press Ctrl+C to stop the server.
echo.

npm run dev
pause
