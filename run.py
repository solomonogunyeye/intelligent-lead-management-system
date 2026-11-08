"""LEADLIFT launcher.

Run from the candidate root:
    python run.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not VENV_PYTHON.exists():
    VENV_PYTHON = Path(sys.executable)

# Prefer the project's virtual environment when available.
if VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve())] + sys.argv[1:])

DB_PATH = ROOT / "leadlift" / "data" / "leadlift.db"
PORT = 8501
URL = f"http://localhost:{PORT}"


def check_port(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    os.environ.setdefault("PYTHONPATH", str(ROOT))
    os.environ.setdefault("DATABASE_PATH", str(DB_PATH))
    os.environ.setdefault("LOG_DIR", str(ROOT / "leadlift" / "data" / "logs"))

    if check_port(PORT):
        print(f"LEADLIFT appears to be already running at {URL}")
        return 0

    try:
        import streamlit  # noqa: F401
    except Exception as exc:
        print(f"Missing dependency: streamlit ({exc})")
        return 1

    try:
        subprocess.run(
            [str(VENV_PYTHON), "-c", "import leadlift.db; leadlift.db.init_db_with_migrations()"],
            check=True,
            cwd=str(ROOT),
            env=os.environ.copy(),
        )
    except subprocess.CalledProcessError as exc:
        print(f"Database initialization failed: {exc}")
        return 1

    cmd = [
        str(VENV_PYTHON),
        "-m",
        "streamlit",
        "run",
        str(ROOT / "leadlift" / "ui.py"),
        "--server.port",
        str(PORT),
        "--server.headless",
        "true",
    ]
    try:
        subprocess.run(cmd, check=True, cwd=str(ROOT), env=os.environ.copy())
    except KeyboardInterrupt:
        print("LEADLIFT stopped.")
        return 0
    except Exception as exc:
        print(f"Startup failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
