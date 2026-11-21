"""Run scripts/mq.py with the host name reported as `demo-pc`, nothing else changed.

Used only for recording the demo, so that the machine's real name (which `status` shows
next to a runner lock) does not appear in published images.

    python demo_host.py <path to mq.py> ARGS...
"""
import runpy
import socket
import sys

socket.gethostname = lambda: "demo-pc"
script = sys.argv[1]
sys.argv = [script] + sys.argv[2:]
runpy.run_path(script, run_name="__main__")
