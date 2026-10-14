@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe edge_real\check_samples.py
pause
