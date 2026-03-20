@echo off
cd /d "%~dp0"
echo Starting Mis-Picking Finder...
echo Access on your phone: http://192.168.0.69:8000
python -m uvicorn main:app --host 0.0.0.0 --port 8000
pause
