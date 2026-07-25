import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SHUTDOWN_TIMEOUT_SECONDS = 5


def _stop(processes: list[subprocess.Popen[bytes]]) -> None:
    running = [process for process in processes if process.poll() is None]

    for process in running:
        os.killpg(process.pid, signal.SIGTERM)

    deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
    for process in running:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def run() -> None:
    commands = (
        (
            "api",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "src.main:app",
                "--app-dir",
                "apps/api",
                "--reload",
            ],
        ),
        ("web", ["npm", "run", "dev"]),
    )
    processes: list[subprocess.Popen[bytes]] = []

    try:
        for name, command in commands:
            print(f"Starting {name}...", flush=True)
            processes.append(subprocess.Popen(command, cwd=PROJECT_ROOT, start_new_session=True))

        while all(process.poll() is None for process in processes):
            time.sleep(0.2)

        exit_code = next(
            process.returncode for process in processes if process.returncode is not None
        )
        if exit_code != 0:
            raise SystemExit(exit_code)
    except KeyboardInterrupt:
        pass
    finally:
        _stop(processes)
