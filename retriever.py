"""
ME School Manual — Retriever
Hybrid search: semantic (embeddings) + keyword (filename & content matching).
"""

from __future__ import annotations
import os
import unicodedata
from dotenv import load_dotenv

load_dotenv()

CHROMA_PATH        = "./chroma_db"
COLLECTION         = "me_school_manual"
MODEL_NAME         = "paraphrase-multilingual-MiniLM-L12-v2"
TOP_K              = 8     # semantic results
DISTANCE_THRESHOLD = 1.20  # drop only completely unrelated chunks
KEYWORD_TOP_K      = 4     # extra chunks added by keyword match


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s).lower()

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

def _keyword_hits(query: str, all_docs, all_metas, seen_paths: set) -> list[dict]:
    """
    Scan all chunks for keyword matches in filename or content.
    Returns up to KEYWORD_TOP_K chunks not already in seen_paths.
    """
    keywords = [w for w in _nfc(query).split() if len(w) >= 3]
    if not keywords:
        return []

    scored = []
    for doc, meta in zip(all_docs, all_metas):
        fp = meta.get("file_path", "")
        if fp in seen_paths:
            continue
        fname = _nfc(meta.get("file_name", ""))
        text  = _nfc(doc)
        # count how many keywords appear in filename or text
        fname_hits = sum(1 for k in keywords if k in fname)
        text_hits  = sum(1 for k in keywords if k in text)
        if fname_hits > 0 or text_hits >= 2:
            scored.append((-(fname_hits * 3 + text_hits), doc, meta))

    scored.sort(key=lambda x: x[0])
    results = []
    added_paths = set()
    for _, doc, meta in scored[:KEYWORD_TOP_K]:
        fp = meta.get("file_path", "")
        if fp in added_paths:
            continue
        added_paths.add(fp)
        results.append({
            "text":      doc,
            "file_name": meta.get("file_name", ""),
            "file_path": fp,
            "url":       meta.get("url", ""),
            "folder":    meta.get("folder", ""),
            "score":     0.0,  # keyword match — treat as high relevance
        })
    return results


def search(query: str, top_k: int = TOP_K) -> list[dict]:
    """
    Hybrid search: semantic embeddings + keyword fallback.
    Returns deduplicated, ranked chunks.
    """
    model      = _get_model()
    collection = _get_collection()

    # ── Semantic search ──────────────────────────────────────────────────────
    embedding = model.encode(query).tolist()
    results = collection.query(
        query_embeddings=[embedding],
        n_results=min(top_k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    seen_paths = set()
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        if dist > DISTANCE_THRESHOLD:
            continue
        fp = meta.get("file_path", "")
        hits.append({
            "text":      doc,
            "file_name": meta.get("file_name", ""),
            "file_path": fp,
            "url":       meta.get("url", ""),
            "folder":    meta.get("folder", ""),
            "score":     round(dist, 4),
        })
        seen_paths.add(fp)

    # ── Keyword fallback: scan ALL chunks for exact term matches ─────────────
    all_data  = collection.get(include=["documents", "metadatas"])
    kw_hits   = _keyword_hits(query, all_data["documents"], all_data["metadatas"], seen_paths)
    hits      = kw_hits + hits   # keyword matches go first

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
