@echo off
REM Double-click to launch Dukanbook (backend + UI).
cd /d "%~dp0"
.venv\Scripts\python.exe run.py
pause
