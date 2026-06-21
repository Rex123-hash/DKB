"""One-command launcher: starts the FastAPI backend and the Streamlit UI together.

    python run.py

Stops both cleanly on Ctrl+C. The Streamlit page opens in your browser.
"""
from __future__ import annotations

import subprocess
import sys
import time
import urllib.request

PY = sys.executable
BACKEND = [PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"]
FRONTEND = [PY, "-m", "streamlit", "run", "ui/streamlit_app.py",
            "--server.port", "8501", "--server.address", "127.0.0.1"]


def _wait_for_backend(timeout: float = 60.0) -> None:
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2)
            return
        except Exception:
            time.sleep(1)
    print("⚠️  Backend did not become healthy in time (continuing anyway).")


def main() -> None:
    print("📒 Starting Dukanbook…")
    print("   backend  → http://127.0.0.1:8000")
    backend = subprocess.Popen(BACKEND)
    frontend = None
    try:
        print("   (first run builds the knowledge base — please wait a moment)")
        _wait_for_backend()
        print("   frontend → http://localhost:8501  (opening browser)")
        frontend = subprocess.Popen(FRONTEND)
        frontend.wait()
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        if frontend is not None:
            frontend.terminate()
        backend.terminate()


if __name__ == "__main__":
    main()
