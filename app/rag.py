"""RAG: chunk the markdown knowledge base, embed it with Gemini, search by cosine similarity.

MVP keeps vectors in SQLite and searches with numpy (fine for a few hundred chunks).
At scale this becomes PostgreSQL + pgvector with an HNSW index.
If embeddings are unavailable (no key / quota), search falls back to keyword overlap,
so the demo never breaks.
"""
import json
import logging
import re

import numpy as np

from app import db, llm
from app.config import KB_DIR

log = logging.getLogger("rag")
MAX_CHUNK_CHARS = 900

_cache: list[dict] | None = None  # chunks loaded in memory for fast search


def chunk_markdown(text: str, source: str) -> list[dict]:
    """Split on '## ' headings; split long sections by paragraph."""
    chunks: list[dict] = []
    sections = re.split(r"\n(?=## )", text)
    for section in sections:
        section = section.strip()
        if not section:
            continue
        lines = section.splitlines()
        heading = lines[0].lstrip("# ").strip()
        body = "\n".join(lines[1:]).strip()
        if not body:
            continue

        current = ""
        for paragraph in body.split("\n\n"):
            if len(current) + len(paragraph) > MAX_CHUNK_CHARS and current:
                chunks.append({"source": source, "heading": heading, "content": current.strip()})
                current = ""
            current += paragraph + "\n\n"
        if current.strip():
            chunks.append({"source": source, "heading": heading, "content": current.strip()})
    return chunks


def ingest() -> dict:
    """Read knowledge_base/*.md, embed every chunk, store in DB. Returns stats."""
    global _cache
    chunks: list[dict] = []
    for path in sorted(KB_DIR.glob("*.md")):
        chunks.extend(chunk_markdown(path.read_text(encoding="utf-8"), path.name))

    embedded = False
    try:
        texts = [f"{c['heading']}\n{c['content']}" for c in chunks]
        vectors = llm.embed(texts)
        for chunk, vector in zip(chunks, vectors):
            chunk["embedding"] = vector
        embedded = True
    except Exception as exc:  # keep working with keyword search
        log.warning("Embedding failed, using keyword search fallback: %s", exc)

    db.replace_kb_chunks(chunks)
    _cache = None
    return {"chunks": len(chunks), "embedded": embedded}


def _load() -> list[dict]:
    global _cache
    if _cache is None:
        rows = db.all_kb_chunks()
        for row in rows:
            if row["embedding_json"]:
                row["vector"] = np.array(json.loads(row["embedding_json"]), dtype=np.float32)
            else:
                row["vector"] = None
        _cache = rows
    return _cache


def _keyword_score(query: str, text: str) -> float:
    query_words = set(re.findall(r"\w+", query.lower()))
    text_words = set(re.findall(r"\w+", text.lower()))
    if not query_words:
        return 0.0
    return len(query_words & text_words) / len(query_words)


def search(query: str, top_k: int = 3) -> list[dict]:
    chunks = _load()
    if not chunks:
        return []

    has_vectors = all(c["vector"] is not None for c in chunks)
    scored: list[tuple[float, dict]] = []

    if has_vectors:
        try:
            query_vector = np.array(llm.embed([query])[0], dtype=np.float32)
            for chunk in chunks:
                vector = chunk["vector"]
                similarity = float(
                    np.dot(query_vector, vector)
                    / (np.linalg.norm(query_vector) * np.linalg.norm(vector) + 1e-9)
                )
                scored.append((similarity, chunk))
        except Exception as exc:
            log.warning("Query embedding failed, falling back to keywords: %s", exc)
            scored = []

    if not scored:
        for chunk in chunks:
            scored.append((_keyword_score(query, chunk["heading"] + " " + chunk["content"]), chunk))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    results = []
    for score, chunk in scored[:top_k]:
        results.append(
            {"source": chunk["source"], "heading": chunk["heading"], "content": chunk["content"], "score": round(score, 3)}
        )
    return results


def ensure_ingested() -> None:
    if not db.all_kb_chunks():
        stats = ingest()
        log.info("Knowledge base ingested: %s", stats)
