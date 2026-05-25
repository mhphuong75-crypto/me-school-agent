#!/usr/bin/env python3
"""
ME School Manual — Document Ingestion Script
Run once (or re-run when files change) to build the vector database.

Usage:
    python ingest.py
    python ingest.py --reset   # wipe and rebuild from scratch
"""

import os, sys, re, hashlib, unicodedata
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

MANUAL_ROOT    = Path(os.getenv("MANUAL_ROOT", "./OneDrive_1_10-05-2026"))
ONEDRIVE_BASE  = os.getenv("ONEDRIVE_BASE_URL", "").rstrip("/")
CHROMA_PATH    = "./chroma_db"
COLLECTION     = "me_school_manual"
CHUNK_WORDS    = 600
CHUNK_OVERLAP  = 80
SUPPORTED_EXT  = {".docx", ".doc", ".pdf", ".xlsx", ".xls", ".pptx", ".ppt", ".txt"}

# ── Text extractors ────────────────────────────────────────────────────────

def extract_docx(path: Path) -> str:
    import docx
    try:
        doc = docx.Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        print(f"  WARN docx: {path.name}: {e}")
        return ""

def extract_pdf(path: Path) -> str:
    import pdfplumber
    try:
        with pdfplumber.open(str(path)) as pdf:
            return "\n".join(
                page.extract_text() or "" for page in pdf.pages
            )
    except Exception as e:
        print(f"  WARN pdf: {path.name}: {e}")
        return ""

def extract_xlsx(path: Path) -> str:
    import openpyxl
    try:
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        rows = []
        for ws in wb.worksheets:
            rows.append(f"[Sheet: {ws.title}]")
            for row in ws.iter_rows():
                vals = [str(c.value) for c in row if c.value is not None]
                if vals:
                    rows.append(" | ".join(vals))
        return "\n".join(rows)
    except Exception as e:
        print(f"  WARN xlsx: {path.name}: {e}")
        return ""

def extract_pptx(path: Path) -> str:
    from pptx import Presentation
    try:
        prs = Presentation(str(path))
        texts = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    texts.append(shape.text)
        return "\n".join(texts)
    except Exception as e:
        print(f"  WARN pptx: {path.name}: {e}")
        return ""

def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".docx", ".doc"):
        return extract_docx(path)
    elif ext == ".pdf":
        return extract_pdf(path)
    elif ext in (".xlsx", ".xls"):
        return extract_xlsx(path)
    elif ext in (".pptx", ".ppt"):
        return extract_pptx(path)
    elif ext == ".txt":
        return path.read_text(errors="ignore")
    return ""

# ── Chunking ───────────────────────────────────────────────────────────────

def chunk(text: str, size=CHUNK_WORDS, overlap=CHUNK_OVERLAP):
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk_words = words[i:i + size]
        chunks.append(" ".join(chunk_words))
        i += size - overlap
    return [c for c in chunks if len(c.strip()) > 30]

# ── File → OneDrive URL ────────────────────────────────────────────────────

def to_url(rel_path: str) -> str:
    if not ONEDRIVE_BASE:
        return f"file://{MANUAL_ROOT / rel_path}"
    # URL-encode only spaces; keep Vietnamese chars readable
    encoded = str(rel_path).replace("\\", "/").replace(" ", "%20")
    return f"{ONEDRIVE_BASE}/{encoded}"

# ── Main ───────────────────────────────────────────────────────────────────

def main():
    import json
    import numpy as np
    from sentence_transformers import SentenceTransformer

    print(f"Manual root : {MANUAL_ROOT}")
    print(f"OneDrive URL: {ONEDRIVE_BASE or '(not set — local paths will be used)'}")
    print()

    if not MANUAL_ROOT.exists():
        print(f"ERROR: MANUAL_ROOT not found: {MANUAL_ROOT}")
        sys.exit(1)

    print("Loading embedding model (first run downloads ~120 MB)…")
    model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    print("Model ready.\n")

    # Collect files
    files = [
        f for f in MANUAL_ROOT.rglob("*")
        if f.is_file()
        and f.suffix.lower() in SUPPORTED_EXT
        and not f.name.startswith((".", "~$", "__TEMP"))
        and f.name != ".DS_Store"
    ]
    print(f"Found {len(files)} files to index.\n")

    all_vectors  = []
    all_records  = []

    for file_idx, fpath in enumerate(files, 1):
        rel    = fpath.relative_to(MANUAL_ROOT)
        folder = rel.parts[0] if len(rel.parts) > 1 else "root"
        print(f"[{file_idx}/{len(files)}] {rel}")

        text = extract_text(fpath)
        if not text.strip():
            print("  → (no text extracted, skipping)")
            continue

        chunks = chunk(text)
        if not chunks:
            continue

        url        = to_url(str(rel))
        embeddings = model.encode(chunks, show_progress_bar=False)

        for i, (c, emb) in enumerate(zip(chunks, embeddings)):
            all_vectors.append(emb)
            all_records.append({
                "text":      c,
                "file_name": fpath.name,
                "file_path": str(rel),
                "folder":    folder,
                "url":       url,
            })

        print(f"  → {len(chunks)} chunks indexed")

    # Save to git-friendly files
    vectors_path  = Path("vectors.npy")
    metadata_path = Path("metadata.json")

    np.save(str(vectors_path), np.array(all_vectors, dtype="float32"))
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False)

    print(f"\nDone. Total chunks: {len(all_records)}")
    print(f"Saved: {vectors_path} ({vectors_path.stat().st_size/1024/1024:.1f} MB)")
    print(f"Saved: {metadata_path} ({metadata_path.stat().st_size/1024/1024:.1f} MB)")

if __name__ == "__main__":
    main()
