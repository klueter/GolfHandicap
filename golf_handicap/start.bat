@echo off
cd /d "%~dp0"

:: Kill any existing app.py process
for /f "tokens=2 delims=," %%a in ('tasklist /fi "imagename eq python.exe" /fo csv /nh 2^>nul') do (
    wmic process where "ProcessId=%%~a and CommandLine like '%%app.py%%'" call terminate >nul 2>&1
)
timeout /t 1 /nobreak >nul

pip install -r requirements.txt -q
start python app.py
timeout /t 2 /nobreak >nul
start firefox http://localhost:5000
