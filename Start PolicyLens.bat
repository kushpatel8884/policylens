@echo off
title PolicyLens
cd /d "%~dp0"
echo Starting PolicyLens... keep this window open while you use the app.
python -m pip install -q -r requirements.txt
start "" cmd /c "timeout /t 5 >nul & start http://localhost:8501"
python -m streamlit run PolicyLens.py
pause
