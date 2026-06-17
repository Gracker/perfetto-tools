@echo off
REM Windows entry: forwards all args to perfetto_capture.py.
REM %~dp0 = directory of this script, with trailing backslash.
setlocal
set "SCRIPT_DIR=%~dp0"
python "%SCRIPT_DIR%perfetto_capture.py" %*
exit /b %ERRORLEVEL%
