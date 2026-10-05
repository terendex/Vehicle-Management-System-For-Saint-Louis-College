@echo off
rem The instructor demo: a local copy of the system on a clock you can move
rem (admin sidebar, Test Clock). Never touches the live database.
rem Same as:  .\dev.ps1 -SimClock -SimSetup   (an existing demo database is kept)
rem To start the demo over from scratch, run:  .\dev.ps1 -SimClock -SimSetup -Reset
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev.ps1" -SimClock -SimSetup
pause
