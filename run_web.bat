@echo off
rem Launch the browser-based video editor (scripts/web_edit.py).
pushd "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python scripts\web_edit.py
) else (
    py scripts\web_edit.py
)
popd
pause
