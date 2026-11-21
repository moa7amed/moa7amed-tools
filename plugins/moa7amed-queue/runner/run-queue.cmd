@echo off
rem Starts the PowerShell queue runner without changing the machine's execution policy.
rem Usage: run-queue.cmd -Project C:\src\my-app [other run-queue.ps1 parameters]
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-queue.ps1" %*
exit /b %ERRORLEVEL%
