@echo off
setlocal
cd /d "%~dp0"
if exist .venv\Scripts\python.exe goto install
where py >nul 2>nul
if errorlevel 1 goto no_launcher
py -3.12 -c "import sys; assert sys.maxsize > 2**32" >nul 2>nul
if not errorlevel 1 goto create
py install --help >nul 2>nul
if errorlevel 1 goto no_runtime
echo Python 3.12 not found. Installing it with Python Install Manager...
py install 3.12
if errorlevel 1 goto no_runtime
:create
py -3.12 -m venv .venv
if errorlevel 1 goto fail
:install
.venv\Scripts\python.exe -c "import tkinter"
if errorlevel 1 goto no_tk
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo.
echo READY. Run start_edge.bat. For synchronization also run start_server.bat.
pause
exit /b 0
:no_launcher
echo Install Python 3.12 64-bit with Python Launcher and Tcl/Tk from python.org.
echo Then run this file again.
pause
exit /b 1
:no_runtime
echo Python 3.12 is not available. Open CMD and run: py install 3.12
echo If this command is unavailable, install Python 3.12 64-bit from python.org.
pause
exit /b 1
:no_tk
echo This Python installation lacks Tcl/Tk. Repair Python and enable Tcl/Tk.
pause
exit /b 1
:fail
echo Setup failed. The error is shown above; capture it before closing this window.
pause
exit /b 1
