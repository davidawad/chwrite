@echo off
setlocal

rem chwrite-setup - Windows launcher (SPEC.md section 32)
rem
rem Locates the real chwrite-setup.py implementation and invokes python/py
rem on it, passing through all arguments and propagating the exit code.
rem
rem Resolution order:
rem   1. %APPDATA%\chwrite\chwrite-setup.py  (rare - see chwrite-setup's
rem      own header comment for why)
rem   2. chwrite-setup.py next to this script

set "INSTALLED=%APPDATA%\chwrite\chwrite-setup.py"
set "LOCAL=%~dp0chwrite-setup.py"

if exist "%INSTALLED%" (
    set "TARGET=%INSTALLED%"
) else if exist "%LOCAL%" (
    set "TARGET=%LOCAL%"
) else (
    echo chwrite-setup: could not find chwrite-setup.py (looked in "%INSTALLED%" and "%LOCAL%") 1>&2
    exit /b 2
)

where python >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    python "%TARGET%" %*
    exit /b %ERRORLEVEL%
)

where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    py "%TARGET%" %*
    exit /b %ERRORLEVEL%
)

echo chwrite-setup: no Python interpreter found (tried "python" and "py") 1>&2
exit /b 2
