@echo off
REM Windows entry: forwards all args to perfetto_capture.py.
REM %~dp0 = directory of this script, with trailing backslash.
setlocal
set "SCRIPT_DIR=%~dp0"
if defined PERFETTO_TOOLS_PYTHON (
  "%PERFETTO_TOOLS_PYTHON%" "%SCRIPT_DIR%perfetto_capture.py" %*
  exit /b %ERRORLEVEL%
)
set "MANAGED_PYTHON=%SCRIPT_DIR%..\.venv\Scripts\python.exe"
if exist "%MANAGED_PYTHON%" (
  "%MANAGED_PYTHON%" "%SCRIPT_DIR%perfetto_capture.py" %*
  exit /b %ERRORLEVEL%
)
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
  py -3 "%SCRIPT_DIR%perfetto_capture.py" %*
  exit /b %ERRORLEVEL%
)
python "%SCRIPT_DIR%perfetto_capture.py" %*
exit /b %ERRORLEVEL%
