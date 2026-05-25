"""
ME School Manual — Retriever
Embeds a query and returns the top-k most relevant document chunks from ChromaDB.
"""

from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

CHROMA_PATH = "./chroma_db"
COLLECTION  = "me_school_manual"
MODEL_NAME  = "paraphrase-multilingual-MiniLM-L12-v2"
TOP_K              = 8     # fetch more candidates so filtering has enough to work with
DISTANCE_THRESHOLD = 0.50  # cosine distance — drop chunks above this (0=identical, 1=unrelated)

# ── Lazy singletons ────────────────────────────────────────────────────────

_client     = None
_collection = None
_model      = None


def _get_collection():
    global _client, _collection
    if _collection is None:
        import chromadb
        _client     = chromadb.PersistentClient(path=CHROMA_PATH)
        _collection = _client.get_collection(COLLECTION)
    return _collection


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


# ── Public API ─────────────────────────────────────────────────────────────

def search(query: str, top_k: int = TOP_K) -> list[dict]:
    """
    Embed *query* and return the top-k most similar chunks.

    Each result dict has keys:
        text      – the chunk text
        file_name – original filename
        file_path – relative path inside the manual root
        url       – OneDrive URL (or file:// local path)
        folder    – top-level section folder name
        score     – cosine distance (lower = more similar; kept for debugging)
    """
    model      = _get_model()
    collection = _get_collection()

    embedding = model.encode(query).tolist()

    results = collection.query(
        query_embeddings=[embedding],
        n_results=min(top_k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    for doc, meta, dist in zip(documents, metadatas, distances):
        if dist > DISTANCE_THRESHOLD:
            continue   # too loosely related — skip
        hits.append({
            "text":      doc,
            "file_name": meta.get("file_name", ""),
            "file_path": meta.get("file_path", ""),
            "url":       meta.get("url", ""),
            "folder":    meta.get("folder", ""),
            "score":     round(dist, 4),
        })

    return hits


def format_context(hits: list[dict]) -> str:
    """
    Build the [CONTEXT] block injected into the system prompt.
    Each chunk is prefixed with its source so the LLM can cite it.
    """
    parts = []
    for i, h in enumerate(hits, 1):
        parts.append(
            f"--- Đoạn {i} | Nguồn: {h['file_name']} ---\n{h['text']}"
        )
    return "\n\n".join(parts)


def unique_sources(hits: list[dict]) -> list[dict]:
    """
    Deduplicate hits by file_path and return a list of
    {'file_name': ..., 'url': ...} dicts for the source footer.
    """
    seen = {}
    for h in hits:
        fp = h["file_path"]
        if fp and fp not in seen:
            seen[fp] = {"file_name": h["file_name"], "url": h["url"]}
    return list(seen.values())
