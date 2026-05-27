#!/usr/bin/env python3
"""
ME School Manual — Document Ingestion Script

Two modes (auto-detected from .env):
  • SharePoint mode  — reads files directly from SharePoint via Microsoft Graph API
                       (used when SHAREPOINT_TENANT_ID etc. are set in .env)
  • Local mode       — reads files from MANUAL_ROOT folder on this machine

Usage:
    python ingest.py
    python ingest.py --reset   # wipe and rebuild from scratch
"""

import os, sys, tempfile
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────

# Local mode
MANUAL_ROOT   = Path(os.getenv("MANUAL_ROOT", "./OneDrive_1_10-05-2026"))
ONEDRIVE_BASE = os.getenv("ONEDRIVE_BASE_URL", "").rstrip("/")

# SharePoint mode (all four must be set to activate SharePoint mode)
SP_TENANT_ID     = os.getenv("SHAREPOINT_TENANT_ID", "")
SP_CLIENT_ID     = os.getenv("SHAREPOINT_CLIENT_ID", "")
SP_CLIENT_SECRET = os.getenv("SHAREPOINT_CLIENT_SECRET", "")
SP_SITE_URL      = os.getenv("SHAREPOINT_SITE_URL", "")
SP_FOLDER        = os.getenv("SHAREPOINT_FOLDER", "")   # relative path inside document library
                                                         # e.g. "Sổ tay/ME School Manual_Sotana"

USE_SHAREPOINT = all([SP_TENANT_ID, SP_CLIENT_ID, SP_CLIENT_SECRET, SP_SITE_URL])

CHUNK_WORDS   = 600
CHUNK_OVERLAP = 80
SUPPORTED_EXT = {".docx", ".doc", ".pdf", ".xlsx", ".xls", ".pptx", ".ppt", ".txt"}


# ── Text extractors ────────────────────────────────────────────────────────

def extract_docx(path: Path) -> str:
    import docx
    try:
        doc = docx.Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        print(f"  WARN docx {path.name}: {e}")
        return ""

def extract_pdf(path: Path) -> str:
    import pdfplumber
    try:
        with pdfplumber.open(str(path)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as e:
        print(f"  WARN pdf {path.name}: {e}")
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
        print(f"  WARN xlsx {path.name}: {e}")
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
        print(f"  WARN pptx {path.name}: {e}")
        return ""

def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in (".docx", ".doc"):   return extract_docx(path)
    elif ext == ".pdf":             return extract_pdf(path)
    elif ext in (".xlsx", ".xls"): return extract_xlsx(path)
    elif ext in (".pptx", ".ppt"): return extract_pptx(path)
    elif ext == ".txt":            return path.read_text(errors="ignore")
    return ""


# ── Chunking ───────────────────────────────────────────────────────────────

def chunk(text: str, size=CHUNK_WORDS, overlap=CHUNK_OVERLAP):
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        chunks.append(" ".join(words[i:i + size]))
        i += size - overlap
    return [c for c in chunks if len(c.strip()) > 30]


# ── SharePoint / Microsoft Graph helpers ──────────────────────────────────

def _graph_token() -> str:
    """Get Microsoft Graph access token via client credentials."""
    from msal import ConfidentialClientApplication
    app = ConfidentialClientApplication(
        SP_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{SP_TENANT_ID}",
        client_credential=SP_CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    if "access_token" not in result:
        raise RuntimeError(
            f"Microsoft authentication failed: {result.get('error_description', result)}"
        )
    return result["access_token"]


def _graph_get(token: str, url: str) -> dict:
    import requests
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    return resp.json()


def _get_drive_id(token: str) -> str:
    """Get the default document library drive ID for the configured SharePoint site."""
    from urllib.parse import urlparse
    parsed   = urlparse(SP_SITE_URL)
    hostname = parsed.netloc
    path     = parsed.path.rstrip("/")
    site     = _graph_get(token, f"https://graph.microsoft.com/v1.0/sites/{hostname}:{path}")
    drive    = _graph_get(token, f"https://graph.microsoft.com/v1.0/sites/{site['id']}/drive")
    return drive["id"]


def _list_files(token: str, drive_id: str, folder: str) -> list:
    """Recursively list all supported files under folder path."""
    if folder:
        encoded = folder.replace(" ", "%20")
        url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{encoded}:/children"
    else:
        url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/children"

    files = []
    while url:
        data = _graph_get(token, url)
        for item in data.get("value", []):
            item_path = f"{folder}/{item['name']}" if folder else item["name"]
            if "folder" in item:
                files.extend(_list_files(token, drive_id, item_path))
            elif "file" in item:
                if Path(item["name"]).suffix.lower() in SUPPORTED_EXT:
                    files.append({
                        "name":         item["name"],
                        "path":         item_path,
                        "download_url": item.get("@microsoft.graph.downloadUrl", ""),
                        "web_url":      item.get("webUrl", ""),
                    })
        url = data.get("@odata.nextLink")   # pagination
    return files


def _download(url: str, dest: Path):
    import requests
    resp = requests.get(url, stream=True)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for block in resp.iter_content(chunk_size=8192):
            f.write(block)


# ── Main — SharePoint mode ─────────────────────────────────────────────────

def main_sharepoint():
    import json
    import numpy as np
    from sentence_transformers import SentenceTransformer

    print("Mode     : SharePoint (Microsoft Graph API)")
    print(f"Site     : {SP_SITE_URL}")
    print(f"Folder   : {SP_FOLDER or '(root of document library)'}")
    print()

    print("Authenticating with Microsoft…")
    token = _graph_token()
    print("✓ Authenticated\n")

    print("Connecting to SharePoint…")
    drive_id = _get_drive_id(token)
    print("✓ Connected\n")

    print("Listing files…")
    files = _list_files(token, drive_id, SP_FOLDER)
    print(f"Found {len(files)} supported files.\n")

    if not files:
        print("ERROR: No files found. Check SHAREPOINT_FOLDER path in .env")
        sys.exit(1)

    print("Loading embedding model (first run downloads ~120 MB)…")
    model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    print("Model ready.\n")

    all_vectors, all_records = [], []

    with tempfile.TemporaryDirectory() as tmpdir:
        for idx, f in enumerate(files, 1):
            print(f"[{idx}/{len(files)}] {f['path']}")

            tmp_path = Path(tmpdir) / f["name"]
            try:
                _download(f["download_url"], tmp_path)
            except Exception as e:
                print(f"  WARN download failed: {e}")
                continue

            text = extract_text(tmp_path)
            if not text.strip():
                print("  → (no text, skipping)")
                continue

            chunks = chunk(text)
            if not chunks:
                continue

            folder_label = f["path"].replace("\\", "/").split("/")[0]
            embeddings   = model.encode(chunks, show_progress_bar=False)

            for c, emb in zip(chunks, embeddings):
                all_vectors.append(emb)
                all_records.append({
                    "text":      c,
                    "file_name": f["name"],
                    "file_path": f["path"],
                    "folder":    folder_label,
                    "url":       f["web_url"],   # real SharePoint link
                })

            print(f"  → {len(chunks)} chunks indexed")

    _save(all_vectors, all_records)


# ── Main — Local mode ──────────────────────────────────────────────────────

def main_local():
    import json
    import numpy as np
    from sentence_transformers import SentenceTransformer

    print("Mode     : Local folder")
    print(f"Root     : {MANUAL_ROOT}")
    print(f"URL base : {ONEDRIVE_BASE or '(not set — file:// links used)'}")
    print()

    if not MANUAL_ROOT.exists():
        print(f"ERROR: MANUAL_ROOT not found: {MANUAL_ROOT}")
        sys.exit(1)

    files = [
        f for f in MANUAL_ROOT.rglob("*")
        if f.is_file()
        and f.suffix.lower() in SUPPORTED_EXT
        and not f.name.startswith((".", "~$", "__TEMP"))
        and f.name != ".DS_Store"
    ]
    print(f"Found {len(files)} files.\n")

    print("Loading embedding model (first run downloads ~120 MB)…")
    model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    print("Model ready.\n")

    all_vectors, all_records = [], []

    for idx, fpath in enumerate(files, 1):
        rel    = fpath.relative_to(MANUAL_ROOT)
        folder = rel.parts[0] if len(rel.parts) > 1 else "root"
        print(f"[{idx}/{len(files)}] {rel}")

        text = extract_text(fpath)
        if not text.strip():
            print("  → (no text, skipping)")
            continue

        chunks = chunk(text)
        if not chunks:
            continue

        encoded    = str(rel).replace("\\", "/").replace(" ", "%20")
        url        = f"{ONEDRIVE_BASE}/{encoded}" if ONEDRIVE_BASE else f"file://{MANUAL_ROOT / rel}"
        embeddings = model.encode(chunks, show_progress_bar=False)

        for c, emb in zip(chunks, embeddings):
            all_vectors.append(emb)
            all_records.append({
                "text":      c,
                "file_name": fpath.name,
                "file_path": str(rel),
                "folder":    folder,
                "url":       url,
            })

        print(f"  → {len(chunks)} chunks indexed")

    _save(all_vectors, all_records)


# ── Save ───────────────────────────────────────────────────────────────────

def _save(all_vectors: list, all_records: list):
    import json
    import numpy as np

    vectors_path  = Path("vectors.npy")
    metadata_path = Path("metadata.json")

    np.save(str(vectors_path), np.array(all_vectors, dtype="float32"))
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False)

    print(f"\n✓ Done.  Total chunks : {len(all_records)}")
    print(f"  Saved  : {vectors_path}  ({vectors_path.stat().st_size/1024/1024:.1f} MB)")
    print(f"  Saved  : {metadata_path} ({metadata_path.stat().st_size/1024/1024:.1f} MB)")


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if USE_SHAREPOINT:
        main_sharepoint()
    else:
        main_local()
