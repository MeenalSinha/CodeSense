@echo off
title CodeSense Backend
color 0A

echo.
echo  ==========================================
echo    CodeSense Backend  ^|  FastAPI Server
echo  ==========================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python not found. Install from https://python.org
    echo          IMPORTANT: Check "Add Python to PATH" during install.
    pause & exit /b 1
)
echo  [OK] Python found:
python --version

if not exist "venv\" (
    echo  [SETUP] Creating virtual environment...
    python -m venv venv
    echo  [OK] venv created.
)

call venv\Scripts\activate.bat

echo  [SETUP] Installing dependencies...
pip install -r requirements.txt -q
if %errorlevel% neq 0 ( echo  [ERROR] pip install failed. & pause & exit /b 1 )
echo  [OK] Dependencies ready.

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo  [INFO] Created .env — edit it to change LLM_BACKEND (mock/ollama/hf)
)

echo.
echo  Starting FastAPI on http://localhost:8000
echo  API Docs: http://localhost:8000/api/docs
echo  Press Ctrl+C to stop.
echo.

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
pause
