@echo off
cd /d "%~dp0"
echo Starting Mis-Picking Finder...
python -m uvicorn main:app --host 0.0.0.0 --port 8000
pause
