@echo off
cd /d "%~dp0"
if not defined MANAGER_PASSWORD set /p MANAGER_PASSWORD=Choose manager password for this server session: 
if not defined MANAGER_PASSWORD exit /b 1
.venv\Scripts\python.exe -m uvicorn server:app --app-dir server --host 0.0.0.0 --port 8000
pause
