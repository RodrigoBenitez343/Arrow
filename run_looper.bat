@echo off
setlocal

set "VENV_DIR=%~dp0.venv"
set "PYTHON=python"

if not exist "%VENV_DIR%\Scripts\python.exe" (
  "%PYTHON%" -m venv "%VENV_DIR%"
)

call "%VENV_DIR%\Scripts\activate.bat"

python -m pip install --upgrade pip
pip install -r "%~dp0requirements.txt"

python "%~dp0LoOper.py"
