#!/usr/bin/env python3
"""Entry point for the moa7amed-queue command line (`moa7amed-queue`)."""
import os
import sys

if sys.version_info < (3, 8):
    sys.stderr.write("moa7amed-queue: Python 3.8 or newer is required (found %s)\n" % sys.version.split()[0])
    sys.exit(127)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    if sys.argv[1:] == ["hook-prompt"]:
        # Runs on every prompt: skip the full CLI unless the prompt is captured.
        from mqlib import hook
        sys.exit(hook.main())
    from mqlib.cli import main
    sys.exit(main())
