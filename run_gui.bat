@echo off
pushd "%~dp0"
python scripts\gui_edit.py %*
set exitcode=%errorlevel%
popd
if %exitcode% neq 0 pause
