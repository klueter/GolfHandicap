@echo off
cd /d "%~dp0"
pip install -r requirements.txt -q
start python app.py
timeout /t 2 /nobreak >nul
start firefox http://localhost:5001
