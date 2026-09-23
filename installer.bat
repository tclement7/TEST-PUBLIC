@echo off
REM Installation locale (une seule fois) : environnement Python isole dans .venv
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv (
  %PY% -m venv .venv || (echo Python 3.11+ est requis. & pause & exit /b 1)
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
REM Installation hors ligne possible : pip install --no-index --find-links=wheelhouse -e .
python -m pip install -e .[dev]
echo.
echo Installation terminee. Lancer "lancer.bat" pour ouvrir l'interface.
pause
