@echo off
python scripts\easy_edit.py %*
if %errorlevel% neq 0 pause
