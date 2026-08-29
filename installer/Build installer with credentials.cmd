@echo off
REM ===========================================================================
REM  Build the installer WITH the campus credentials baked in.
REM
REM  Run this ONCE. The .exe it produces is what you hand out - the people you
REM  give it to just double-click that. They never run this script and never
REM  type any credentials.
REM
REM  You only need to run it again if something in installer\ changes.
REM
REM  THE RESULTING .EXE IS AS SENSITIVE AS THE CREDENTIALS THEMSELVES. It is
REM  obfuscated, not encrypted - anyone holding it can recover the Neon
REM  database URL and the JWT signing key. Give it only to people you would
REM  give database access.
REM ===========================================================================
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "installer\build.ps1" -EmbedCredentials
if errorlevel 1 goto :done

REM A spare copy outside installer\out, so a later plain rebuild in that folder
REM cannot cost you this file. This is the one to hand out.
set "KEEP=%USERPROFILE%\Desktop\SLC-Smart-Parking-Campus-Setup.exe"
copy /Y "installer\out\SLC-Smart-Parking-Campus-Setup.exe" "%KEEP%" >nul 2>&1
if exist "%KEEP%" (
  echo.
  echo   A copy you can hand out has been placed on your Desktop:
  echo     %KEEP%
)

:done
echo.
echo Done. Press any key to close.
pause >nul