@echo off
rem moa7amed-queue command line launcher (cmd.exe and PowerShell on Windows).
rem Finds a Python 3.8+ interpreter and runs scripts\mq.py with the given arguments.
rem Set MOA7AMED_QUEUE_PYTHON to force a specific interpreter (CTQ_PYTHON, the name used
rem before the rebrand, is still honoured).
setlocal
set "MQ_SCRIPT=%~dp0..\scripts\mq.py"
set "MQ_PROBE=import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"
if not defined MOA7AMED_QUEUE_PYTHON if defined CTQ_PYTHON set "MOA7AMED_QUEUE_PYTHON=%CTQ_PYTHON%"

if defined MOA7AMED_QUEUE_PYTHON (
  "%MOA7AMED_QUEUE_PYTHON%" "%MQ_SCRIPT%" %*
  exit /b
)
python -c "%MQ_PROBE%" >nul 2>nul
if not errorlevel 1 (
  python "%MQ_SCRIPT%" %*
  exit /b
)
py -3 -c "%MQ_PROBE%" >nul 2>nul
if not errorlevel 1 (
  py -3 "%MQ_SCRIPT%" %*
  exit /b
)
python3 -c "%MQ_PROBE%" >nul 2>nul
if not errorlevel 1 (
  python3 "%MQ_SCRIPT%" %*
  exit /b
)
echo moa7amed-queue: Python 3.8 or newer was not found. Install Python or set MOA7AMED_QUEUE_PYTHON to its path. 1>&2
exit /b 127
