@echo off
rem Backward-compatible alias of moa7amed-queue.
rem The command was called "ctq" in versions 0.1.0 and 0.2.0 (claude-task-queue).
call "%~dp0moa7amed-queue.cmd" %*
exit /b %ERRORLEVEL%
