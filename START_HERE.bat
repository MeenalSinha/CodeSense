@echo off
title CodeSense — Launch All
color 0E

echo.
echo  ==========================================
echo    CodeSense — Starting Full Stack
echo  ==========================================
echo.
echo  Opening two windows:
echo    Window 1: Backend  (port 8000)
echo    Window 2: Frontend (port 3000)
echo.
echo  Then open: http://localhost:3000
echo.

timeout /t 2 /nobreak >nul
start "CodeSense Backend"  cmd /k "scripts\run_backend.bat"
timeout /t 6 /nobreak >nul
start "CodeSense Frontend" cmd /k "scripts\run_frontend.bat"

echo  Both servers launching...
echo  Open http://localhost:3000 in your browser.
echo.
pause
