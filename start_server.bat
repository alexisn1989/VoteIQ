@echo off
cd /d "c:\Users\Alexis\OneDrive\Desktop\Vriginia_api_election"
REM Kill any processes on port 8000
FOR /F "tokens=5" %%a in ('netstat -ano ^| find ":8000"') do taskkill /PID %%a /F 2>nul
timeout /t 2
REM Start the server with Python
python main.py
