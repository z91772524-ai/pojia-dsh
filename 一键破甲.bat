@echo off
rem ==================================================================
rem  POJIA-DSH v1.0  --  launcher
rem
rem  This file is deliberately 100%% ASCII.  A .bat that contains any
rem  non-ASCII byte gets mangled when cmd.exe parses it under the wrong
rem  code page, and the whole script falls apart.  All Chinese text
rem  lives in the .md docs and inside the Python file.
rem
rem  The target .py has a non-ASCII name, so it is located by wildcard
rem  below -- never write that name into this file.
rem ==================================================================

cd /d "%~dp0"

set "SCRIPT="
for %%F in ("%~dp0*.py") do if not defined SCRIPT set "SCRIPT=%%~fF"

if not defined SCRIPT (
    echo.
    echo   [!] No .py file found next to this launcher.
    echo       Keep the .bat and the .py in the same folder.
    echo.
    pause
    exit /b 1
)

rem ---- 1) python on PATH ----
where python >nul 2>&1
if %errorlevel%==0 set "PYEXE=python"

rem ---- 2) Windows py launcher ----
if not defined PYEXE (
    where py >nul 2>&1
    if %errorlevel%==0 set "PYEXE=py"
)

if not defined PYEXE (
    echo.
    echo   [!] Python not found.
    echo.
    echo       Install Python 3.8 or newer from:
    echo         https://www.python.org/downloads/
    echo       Remember to tick  "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

rem switch the console to UTF-8 so the Python side prints Chinese correctly
chcp 65001 >nul

"%PYEXE%" "%SCRIPT%" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo   [exit code %RC%]
    pause
)
