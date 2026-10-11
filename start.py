"""Supervise both servers; stop the service if either server exits."""
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time


def main():
    os.chdir(Path(__file__).resolve().parent)
    environment = os.environ.copy()
    environment['SEARXNG_SECRET'] = secrets.token_hex(32)
    environment.setdefault('SEARXNG_SETTINGS_PATH', str(Path('searxng-settings.yml').resolve()))
    children = []
    stopping = False

    def stop(_signum=None, _frame=None):
        nonlocal stopping
        stopping = True
        for child in children:
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for config, application in (
            ('searxng-gunicorn.conf.py', 'searxng_backend:app'),
            ('gunicorn.conf.py', 'app:app'),
        ):
            children.append(subprocess.Popen(
                [sys.executable, '-m', 'gunicorn', '-c', config, application], env=environment))
        print('Portal and local search backend starting.', flush=True)
        while not stopping:
            if any(child.poll() is not None for child in children):
                print('A server exited. Inspect startup logs.', flush=True)
                stop()
                return 1
            time.sleep(0.25)
        return 0
    finally:
        stop()
        deadline = time.monotonic() + 8
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    raise SystemExit(main())
