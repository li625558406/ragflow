@echo off
REM RAGFlow Backend Startup Script for Windows

REM Activate virtual environment
call .venv\Scripts\activate.bat

REM Set PYTHONPATH to current directory
set PYTHONPATH=%CD%

REM Set NLTK data directory
set NLTK_DATA=%CD%\nltk_data

REM Enable sandbox and point to Docker-hosted sandbox-executor-manager
set SANDBOX_ENABLED=1
set SANDBOX_HOST=localhost

REM Skip document parsing during crawl (remove this line to re-enable)
set SKIP_PARSE=1

echo Starting RAGFlow backend services...
echo PYTHONPATH=%PYTHONPATH%
echo Using Python:
python --version
echo.

REM Task executor concurrency
set MAX_CONCURRENT_TASKS=5

REM Start task executors in background
echo Starting task_executor_0...
start "RAGFlow Task Executor 0" python rag\svr\task_executor.py 0
echo Starting task_executor_1...
start "RAGFlow Task Executor 1" python rag\svr\task_executor.py 1

REM Start scheduled task executor in background
echo Starting scheduled_task_executor...
start "RAGFlow Scheduled Task Executor" python rag\svr\scheduled_task_executor.py

REM Wait a moment for task executors to start
timeout /t 3 /nobreak >nul

REM Start main server
echo Starting ragflow_server...
python api\ragflow_server.py

REM If server exits, clean up
echo.
echo Server stopped. Press any key to close task executor window...
pause
