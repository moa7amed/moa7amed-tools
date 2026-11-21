#!/bin/sh
# moa7amed-queue runner for macOS, Linux and Git Bash: executes the queued
# tasks of one project, one after another.
#
#   runner/run-queue.sh --project /path/to/project [options]
#
# This is the portable runner (`moa7amed-queue run`). Run it with --help for the options;
# they mirror the parameters of the PowerShell runner (run-queue.ps1).
# $0 may use backslashes when a Windows path is passed to sh.
case $0 in *[/\\]*) here=${0%[/\\]*} ;; *) here=. ;; esac
exec sh "$here/../bin/moa7amed-queue" run "$@"
