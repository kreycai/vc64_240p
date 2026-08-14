@echo off
REM Gera dist\vc64_240p.exe standalone.
REM Precisa de: Python 3.8+, pip install cryptography pyinstaller
cd /d "%~dp0.."
python -m PyInstaller --noconfirm --onefile --noconsole --clean ^
  --name "vc64_240p" ^
  --hidden-import cryptography ^
  --distpath dist ^
  --workpath build\_work ^
  --specpath build ^
  src\vc64_240p_gui.py
echo.
echo Pronto: dist\vc64_240p.exe
pause
