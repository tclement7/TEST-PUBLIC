@echo off
REM Contrôle sans interface (planificateur de tâches Windows) : import, calcul, exports PDF et Excel
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m opcvm_ratios run || exit /b 1
python -m opcvm_ratios export --format both
