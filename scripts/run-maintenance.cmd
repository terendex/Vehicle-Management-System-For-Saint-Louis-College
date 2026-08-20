@echo off
REM ---------------------------------------------------------------------------
REM run-maintenance.cmd - the daily maintenance job, for Task Scheduler.
REM
REM The three jobs (event rollover, expired-account archiving, retention purge)
REM are defined as Celery tasks on a beat schedule, but neither a worker nor a
REM beat process is deployed - see CAMPUS_SETUP.md. `manage.py run_maintenance`
REM runs all three in-process with no broker, and this wrapper is what the
REM scheduled task invokes.
REM
REM A wrapper rather than putting the command in schtasks /TR directly: the repo
REM path contains spaces, and the nested quoting schtasks needs for that is both
REM fragile and easy to break on the next move. This resolves the repo from its
REM own location instead, so the task keeps working if the checkout moves.
REM
REM Output is appended to backend\maintenance.log (gitignored) - a scheduled task
REM has no console, so without this a failure would leave no trace at all.
REM ---------------------------------------------------------------------------
setlocal
set "REPO=%~dp0.."
set "PYTHON=%REPO%\backend\venv\Scripts\python.exe"
set "LOG=%REPO%\backend\maintenance.log"

if not exist "%PYTHON%" (
    echo [%DATE% %TIME%] FAILED: no virtualenv at "%PYTHON%" >> "%LOG%"
    exit /b 1
)

cd /d "%REPO%\backend"
echo. >> "%LOG%"
echo [%DATE% %TIME%] run_maintenance starting >> "%LOG%"
REM %* is passed through so the same wrapper can be run by hand for a dry check:
REM     scripts\run-maintenance.cmd --skip-purge
"%PYTHON%" manage.py run_maintenance %* >> "%LOG%" 2>&1

REM Captured immediately: the echo below is itself a command, and a redirected
REM echo can overwrite ERRORLEVEL before `exit /b` ever reads it.
set "RC=%ERRORLEVEL%"
echo [%DATE% %TIME%] run_maintenance exited with %RC% >> "%LOG%"
exit /b %RC%
