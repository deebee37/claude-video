@echo off
python scripts\gui_edit.py %*
if %errorlevel% neq 0 pause
