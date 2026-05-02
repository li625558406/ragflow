@echo off
REM RAGFlow Backend Startup Script for Windows

REM Activate virtual environment
call .venv\Scripts\activate.bat

REM Set PYTHONPATH to current directory
set PYTHONPATH=%CD%

REM Set NLTK data directory
set NLTK_DATA=%CD%\nltk_data

echo Starting RAGFlow backend services...
echo PYTHONPATH=%PYTHONPATH%
echo Using Python:
python --version
echo.

REM Start task executor in background
echo Starting task_executor...
start "RAGFlow Task Executor" python rag\svr\task_executor.py 0

REM Wait a moment for task executor to start
timeout /t 2 /nobreak >nul

REM Start main server
echo Starting ragflow_server...
python api\ragflow_server.py

REM If server exits, clean up
echo.
echo Server stopped. Press any key to close task executor window...
pause
