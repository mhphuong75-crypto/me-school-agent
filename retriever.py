"""
ME School Manual — Retriever
Hybrid search: numpy cosine similarity + keyword filename matching.
No ChromaDB — uses vectors.npy + metadata.json (git-friendly).
"""

from __future__ import annotations
import json
import unicodedata
import numpy as np
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

VECTORS_PATH  = Path("vectors.npy")
METADATA_PATH = Path("metadata.json")
MODEL_NAME    = "paraphrase-multilingual-MiniLM-L12-v2"
TOP_K              = 8     # semantic candidates to consider
KEYWORD_TOP_K      = 4     # keyword match results
SEM_MAX_DISTANCE   = 0.50  # only keep semantic hits with distance ≤ this (similarity ≥ 0.50)

# ── Synonym expansion (English → Vietnamese common school terms) ────────────
# When staff type English terms, these map to the Vietnamese equivalents
# used in document file names and content.
SYNONYMS: dict[str, list[str]] = {
    "onboarding":   ["hội nhập", "nhân viên mới", "lộ trình"],
    "offboarding":  ["nghỉ việc", "thôi việc", "quy trình nghỉ"],
    "kpi":          ["đánh giá", "định kì", "kết quả"],
    "bep":          ["bếp", "bán trú", "nấu ăn", "thực phẩm"],
    "kitchen":      ["bếp", "bán trú", "nấu ăn", "thực phẩm"],
    "safety":       ["an toàn", "sức khỏe", "tai nạn", "khẩn cấp"],
    "tuition":      ["học phí", "tuyển sinh", "thu phí"],
    "fee":          ["học phí", "tuyển sinh", "thu phí"],
    "hr":           ["nhân sự", "tuyển dụng", "định biên"],
    "policy":       ["chính sách", "quy định", "quy trình"],
    "procedure":    ["quy trình", "quy định", "biểu mẫu"],
    "finance":      ["tài chính", "kế toán", "ngân sách"],
    "event":        ["sự kiện", "tổ chức", "hoạt động"],
    "curriculum":   ["chương trình", "giáo án", "học tập"],
}

# ── Lazy singletons ────────────────────────────────────────────────────────

_model    = None
_vectors  = None
_records  = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _get_index():
    global _vectors, _records
    if _vectors is None:
        _vectors = np.load(str(VECTORS_PATH))                        # (N, 384)
        with open(METADATA_PATH, encoding="utf-8") as f:
            _records = json.load(f)
    return _vectors, _records


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s).lower()


# ── Search ─────────────────────────────────────────────────────────────────

def _expand_query(query: str) -> list[str]:
    """Return extra keywords by expanding English terms to Vietnamese synonyms."""
    q_lower = query.lower()
    extra = []
    for eng, viet_list in SYNONYMS.items():
        if eng in q_lower:
            extra.extend(viet_list)
    return extra


def search(query: str, top_k: int = TOP_K) -> list[dict]:
    """Hybrid: keyword filename match first, then cosine similarity."""
    model             = _get_model()
    vectors, records  = _get_index()

    # ── 1. Keyword match on filename + text ────────────────────────────────
    base_keywords = [w for w in _nfc(query).split() if len(w) >= 3]
    extra_keywords = [_nfc(w) for w in _expand_query(query)]
    keywords = list(dict.fromkeys(base_keywords + extra_keywords))  # dedupe, keep order

    seen_paths   = set()
    kw_hits      = []

    if keywords:
        scored = []
        for i, rec in enumerate(records):
            fname      = _nfc(rec.get("file_name", ""))
            text       = _nfc(rec.get("text", ""))
            fname_hits = sum(1 for k in keywords if k in fname)
            text_hits  = sum(1 for k in keywords if k in text)
            if fname_hits > 0 or text_hits >= 1:   # lowered from 2 → 1 for text hits
                scored.append((-(fname_hits * 3 + text_hits), i))

        scored.sort(key=lambda x: x[0])
        added = set()
        for _, i in scored:
            fp = records[i].get("file_path", "")
            if fp not in added:
                added.add(fp)
                seen_paths.add(fp)
                kw_hits.append({**records[i], "score": 0.0})
            if len(kw_hits) >= KEYWORD_TOP_K:
                break

    # ── 2. Cosine similarity ────────────────────────────────────────────────
    q_vec  = model.encode(query).astype("float32")
    q_norm = q_vec / (np.linalg.norm(q_vec) + 1e-9)
    norms  = np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9
    sims   = (vectors / norms) @ q_norm          # cosine similarity (higher = better)

    top_idx = np.argsort(-sims)[:top_k * 3]      # fetch extra, filter dupes
    sem_hits = []
    for i in top_idx:
        dist = float(1 - sims[i])
        if dist > SEM_MAX_DISTANCE:
            continue   # not similar enough — skip
        fp = records[i].get("file_path", "")
        if fp in seen_paths:
            continue
        seen_paths.add(fp)
        sem_hits.append({**records[i], "score": round(dist, 4)})
        if len(sem_hits) >= top_k:
            break

    return kw_hits + sem_hits


# ── Formatting ─────────────────────────────────────────────────────────────

def format_context(hits: list[dict]) -> str:
    """Build context for Claude. File names are hidden to prevent Claude
    from listing them in the answer — sources are shown separately in the UI."""
    parts = []
    for i, h in enumerate(hits, 1):
        parts.append(f"--- Đoạn {i} ---\n{h['text']}")
    return "\n\n".join(parts)


def unique_sources(hits: list[dict], max_sources: int = 2) -> list[dict]:
    """Return max 2 most relevant sources — keyword matches first, then high-confidence semantic."""
    seen   = {}
    # Priority 1: keyword matches
    for h in hits:
        if len(seen) >= max_sources:
            break
        fp = h.get("file_path", "")
        if fp and fp not in seen and h.get("score", 1.0) == 0.0:
            seen[fp] = {"file_name": h["file_name"], "url": h.get("url", "")}
    # Priority 2: strong semantic matches (fill up to max_sources)
    for h in hits:
        if len(seen) >= max_sources:
            break
        fp    = h.get("file_path", "")
        score = h.get("score", 1.0)
        if fp and fp not in seen and score < 0.30:
            seen[fp] = {"file_name": h["file_name"], "url": h.get("url", "")}
    return list(seen.values())
