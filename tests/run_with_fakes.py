"""Run the web app with FAKE Sarvam + Gemini (no API keys, no credits used).

For UI development/testing only - the agent replies are scripted.
    python -m tests.run_with_fakes      then open http://localhost:8000
"""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "fake.db"))
os.environ.setdefault("GEMINI_API_KEY", "fake")
os.environ.setdefault("SARVAM_API_KEY", "fake")

import uvicorn  # noqa: E402

from tests import fakes  # noqa: E402

fakes.install()

if __name__ == "__main__":
    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
