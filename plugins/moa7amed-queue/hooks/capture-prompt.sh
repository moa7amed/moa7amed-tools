#!/bin/sh
# UserPromptSubmit hook for moa7amed-queue.
#
# A prompt that starts with the capture prefix (default "queue:") is saved as a
# persistent task and blocked, so it never reaches Claude and cannot interrupt
# the work in progress. Every other prompt passes through untouched.
#
# This hook runs on every prompt, so the common case (the default prefix does
# not occur anywhere in the input) is decided here without starting Python.
#
# CTQ_* variables and the ~/.claude-task-queue directory are the names used
# before the rebrand; they are still honoured.

# Tasks started by the queue runner are never captured.
[ -n "${MOA7AMED_QUEUE_RUNNER:-}${CTQ_RUNNER:-}" ] && exit 0

# $0 may use backslashes when a Windows path is passed to sh.
case $0 in *[/\\]*) here=${0%[/\\]*} ;; *) here=. ;; esac
base=${USERPROFILE:-$HOME}
if [ -n "${MOA7AMED_QUEUE_HOME:-}" ]; then
  home=$MOA7AMED_QUEUE_HOME
elif [ -n "${CTQ_HOME:-}" ]; then
  home=$CTQ_HOME
elif [ ! -d "$base/.moa7amed-queue" ] && [ -d "$base/.claude-task-queue" ]; then
  home=$base/.claude-task-queue
else
  home=$base/.moa7amed-queue
fi
input=$(cat)

# The prefix is "queue:" unless an environment variable or the settings file says
# otherwise. With neither, a prompt without that text cannot match. The settings file
# is searched with the shell itself, so that having other settings costs no extra
# process on every prompt.
custom=
if [ -n "${MOA7AMED_QUEUE_CAPTURE_PREFIX+set}" ] || [ -n "${CTQ_CAPTURE_PREFIX+set}" ]; then
  custom=1
elif [ -f "$home/config.json" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case $line in *capture_prefix*) custom=1; break ;; esac
  done < "$home/config.json"
fi
# While a queued task waits for the owner's decision on a concern, a marker file exists;
# then the prompt may be that decision, and Python reads it (it never blocks the prompt).
decisions=
for marker in "$home"/open-decisions/*; do
  [ -e "$marker" ] && decisions=1
  break
done
if [ -z "$custom" ] && [ -z "$decisions" ]; then
  case $input in
    *[Qq][Uu][Ee][Uu][Ee]:*) ;;
    *) exit 0 ;;
  esac
fi

printf '%s' "$input" | sh "$here/../bin/moa7amed-queue" hook-prompt
