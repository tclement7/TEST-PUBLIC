@echo off
REM Lance l'interface web locale (http://127.0.0.1:8765)
cd /d "%~dp0"
call .venv\Scripts\activate.bat
start "" http://127.0.0.1:8765
python -m opcvm_ratios serve
