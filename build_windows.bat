@echo off
REM Builds dist\DepositSlipTracker.exe. Run on Windows with Python 3.11+ installed.
cd /d "%~dp0"
python -m venv .venv || goto :error
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller || goto :error
pyinstaller --noconfirm --onefile --windowed --name DepositSlipTracker ^
  --collect-submodules pillow_heif --collect-data rapidocr_onnxruntime run.py || goto :error
echo.
echo Built dist\DepositSlipTracker.exe
goto :eof
:error
echo Build failed.
exit /b 1
