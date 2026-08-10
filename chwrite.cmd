@echo off
setlocal

rem chwrite - Windows launcher
rem
rem Locates the real chwrite.py implementation and invokes python/py on it,
rem passing through all arguments and propagating the exit code.
rem
rem Resolution order:
rem   1. %APPDATA%\chwrite\chwrite.py   (installed via `chwrite.py install`)
rem   2. chwrite.py next to this script (running from a repo checkout)

set "INSTALLED=%APPDATA%\chwrite\chwrite.py"
set "LOCAL=%~dp0chwrite.py"

if exist "%INSTALLED%" (
    set "TARGET=%INSTALLED%"
) else if exist "%LOCAL%" (
    set "TARGET=%LOCAL%"
) else (
    echo chwrite: could not find chwrite.py (looked in "%INSTALLED%" and "%LOCAL%") 1>&2
    echo chwrite: run "python chwrite.py install" first 1>&2
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

echo chwrite: no Python interpreter found (tried "python" and "py") 1>&2
exit /b 2
