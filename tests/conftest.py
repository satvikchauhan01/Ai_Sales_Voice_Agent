"""Test setup: isolated temp database and fake API keys, so tests never call paid/real APIs."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = str(Path(_tmp) / "test.db")
os.environ["GEMINI_API_KEY"] = "test-key"
os.environ["SARVAM_API_KEY"] = "test-key"
os.environ["HUBSPOT_ACCESS_TOKEN"] = ""
os.environ["CAL_API_KEY"] = ""
