@echo off
REM Start the FastAPI backend and Streamlit frontend (Windows)
REM Run from the app directory: run.bat

set APP_DIR=%~dp0
set ROOT_DIR=%APP_DIR%..

echo === Investment RAG System ===
echo App dir:  %APP_DIR%
echo Root dir: %ROOT_DIR%
echo.

REM Install dependencies
pip install -q --upgrade -r "%APP_DIR%requirements.txt"

REM Start backend in a new window
echo Starting FastAPI backend on http://localhost:8000 ...
start "FastAPI Backend" cmd /k "cd /d %APP_DIR% && uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload"

REM Wait briefly
timeout /t 3 /nobreak >nul

REM Start Streamlit in this window
echo Starting Streamlit frontend on http://localhost:8501 ...
cd /d %APP_DIR%
streamlit run frontend\Home.py --server.port 8501
