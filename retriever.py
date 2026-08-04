"""
ME School Manual — Retriever
Hybrid search: numpy cosine similarity + keyword filename matching + LLM TOC.
No ChromaDB — uses vectors.npy + metadata.json + toc.json (git-friendly).
"""

from __future__ import annotations
import json, os
import unicodedata
import numpy as np
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

VECTORS_PATH  = Path("vectors.npy")
METADATA_PATH = Path("metadata.json")
TOC_PATH      = Path("toc.json")
MODEL_NAME    = "paraphrase-multilingual-MiniLM-L12-v2"
TOC_MODEL     = "claude-haiku-4-5"
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
    "teacher":      ["giáo viên", "cô giáo"],
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


_toc = None

def _get_toc() -> list | None:
    global _toc
    if _toc is None and TOC_PATH.exists():
        with open(TOC_PATH, encoding="utf-8") as f:
            _toc = json.load(f)
    return _toc


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s).lower()


def _strip_accents(s: str) -> str:
    """Remove Vietnamese diacritics so 'giáo' matches 'giao' in filenames."""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


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
        keywords_stripped = [_strip_accents(k) for k in keywords]
        scored = []
        for i, rec in enumerate(records):
            fname      = _nfc(rec.get("file_name", ""))
            fname_bare = _strip_accents(rec.get("file_name", ""))
            text       = _nfc(rec.get("text", ""))
            # Accent-insensitive filename match (giáo matches giao)
            fname_hits = sum(1 for k, ks in zip(keywords, keywords_stripped)
                            if k in fname or ks in fname_bare)
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


# ── TOC-based search (LLM-powered) ────────────────────────────────────────

def search_toc(query: str, api_key: str = "") -> list[dict]:
    """
    Use Claude Haiku to pick relevant files from toc.json, then return
    all chunks from those files. Falls back to empty list on any failure.
    """
    toc = _get_toc()
    if not toc:
        return []

    key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return []

    # Build compact TOC text for the prompt
    toc_text_parts = []
    for i, entry in enumerate(toc):
        qs = "; ".join(entry.get("questions", [])[:5])
        toc_text_parts.append(
            f"[{i}] {entry['file_path']}\n"
            f"    Tóm tắt: {entry.get('summary', 'N/A')}\n"
            f"    Câu hỏi: {qs}"
        )
    toc_text = "\n".join(toc_text_parts)

    prompt = f"""Bạn là hệ thống tìm kiếm tài liệu ME School. Dưới đây là danh mục tài liệu:

{toc_text}

CÂU HỎI CỦA NHÂN VIÊN: {query}

NHIỆM VỤ: Chọn các tài liệu CÓ KHẢ NĂNG chứa câu trả lời. Trả về JSON:
{{"file_indices": [danh sách số thứ tự tài liệu, ví dụ [0, 5, 12]]}}

QUY TẮC:
- Chọn 1-5 tài liệu liên quan nhất
- Ưu tiên tài liệu có câu hỏi mẫu gần với câu hỏi nhân viên
- Nếu không chắc, chọn rộng hơn (nhiều file hơn)
- CHỈ trả về JSON, không text khác"""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=TOC_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        if raw.endswith("```"):
            raw = raw[:-3]
        data = json.loads(raw.strip())
        indices = data.get("file_indices", [])

        # Collect file_paths from selected TOC entries
        selected_paths = set()
        for idx in indices:
            if 0 <= idx < len(toc):
                selected_paths.add(toc[idx]["file_path"])

        if not selected_paths:
            return []

        # Return all chunks matching those file_paths
        _, records = _get_index()
        hits = []
        for rec in records:
            if rec.get("file_path", "") in selected_paths:
                hits.append({**rec, "score": 0.0})  # score 0 = high confidence

        return hits

    except Exception as e:
        print(f"TOC search error: {e}")
        return []


def has_toc() -> bool:
    """Check if toc.json exists and is loaded."""
    return _get_toc() is not None and len(_get_toc()) > 0


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
