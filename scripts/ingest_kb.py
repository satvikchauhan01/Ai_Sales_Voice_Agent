"""Re-build the RAG index after editing files in knowledge_base/.

    python -m scripts.ingest_kb
(or call POST /api/kb/reingest while the server runs)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, rag  # noqa: E402

if __name__ == "__main__":
    db.init_db()
    print(rag.ingest())
