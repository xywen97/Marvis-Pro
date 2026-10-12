"""Native OpenViking entry point; EOF from its owning engine stops the service."""
from __future__ import annotations
import os
import signal
import sys
import threading


def main():
    # The parent alone owns the write end. A crash/SIGKILL also closes it, so
    # OpenViking cannot survive as an orphan and hold the database lock forever.
    def watch_parent():
        sys.stdin.buffer.read()
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=watch_parent, daemon=True, name='marvis-parent-watch').start()
    from openviking_cli.server_bootstrap import main as run_server
    run_server()


if __name__ == '__main__': main()
