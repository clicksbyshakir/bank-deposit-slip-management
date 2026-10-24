@echo off
REM Builds the app and (if Inno Setup 6 is installed) the installer into dist\.
REM Needs Python 3.11+ from python.org. Inno Setup: https://jrsoftware.org/isdl.php
cd /d "%~dp0"
python -m venv .venv || goto :error
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller || goto :error
python build.py || goto :error
goto :eof
:error
echo Build failed.
exit /b 1
