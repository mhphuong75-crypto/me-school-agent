# ME School — Trợ lý nội bộ

Chatbot tra cứu tài liệu vận hành ME School, chạy trên Streamlit.  
Chỉ trả lời dựa trên bộ tài liệu nội bộ (RAG) — không dùng thông tin bên ngoài.

---

## Kiến trúc

```
Tài liệu (docx/pdf/xlsx/pptx)
        │
        ▼
  ingest.py  ──►  ChromaDB (chroma_db/)
                       │
                       ▼
  app.py  ──►  retriever.py  ──►  Claude API  ──►  Streamlit UI
```

- **ingest.py** — đọc tất cả file trong `MANUAL_ROOT`, trích xuất text, tạo embeddings (`paraphrase-multilingual-MiniLM-L12-v2`), lưu vào ChromaDB.
- **retriever.py** — nhận câu hỏi, tìm top-6 chunk liên quan nhất.
- **app.py** — giao diện chat Streamlit; kiểm tra câu hỏi có cần làm rõ không, gọi Claude với context tìm được.
- **prompts.py** — system prompt tiếng Việt, quy tắc chỉ dùng tài liệu nội bộ.

---

## Cài đặt (máy local)

### 1. Yêu cầu

- Python 3.10+
- Tài khoản Anthropic (lấy API key tại [console.anthropic.com](https://console.anthropic.com))

### 2. Cài thư viện

```bash
cd "ME School/me-school-agent"
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Cấu hình `.env`

```bash
cp .env.example .env
```

Mở `.env` và điền:

```
ANTHROPIC_API_KEY=sk-ant-...           # bắt buộc
MANUAL_ROOT=/đường/dẫn/đến/OneDrive_1_10-05-2026   # bắt buộc
ONEDRIVE_BASE_URL=                     # tuỳ chọn (xem bên dưới)
APP_PASSWORD=                          # tuỳ chọn — để trống = không cần mật khẩu
```

#### Lấy `ONEDRIVE_BASE_URL` (để link file có thể click được)

1. Mở OneDrive → chuột phải vào folder `OneDrive_1_10-05-2026` → **Share** → **Anyone with the link can view**
2. Copy link, dán vào `ONEDRIVE_BASE_URL`.  
   Ví dụ: `https://d.docs.live.net/abc123/ME%20School%20Manual`

Nếu để trống, link sẽ là `file://` — chỉ mở được trên máy chạy app.

### 4. Build vector database

```bash
python ingest.py
```

Lần đầu sẽ tải model embedding (~120 MB).  
Chạy lại với `--reset` để xoá và build lại hoàn toàn:

```bash
python ingest.py --reset
```

### 5. Chạy app

```bash
streamlit run app.py
```

Mở trình duyệt tại `http://localhost:8501`.

---

## Deploy lên Streamlit Community Cloud (miễn phí, public URL)

### Bước 1 — Đẩy code lên GitHub

```bash
git init
git add requirements.txt app.py ingest.py retriever.py prompts.py .env.example README.md
git commit -m "Initial ME School agent"
git remote add origin https://github.com/<your-org>/me-school-agent.git
git push -u origin main
```

> **Không commit** `.env`, `chroma_db/`, hoặc folder tài liệu.  
> Thêm vào `.gitignore`:
> ```
> .env
> chroma_db/
> OneDrive_1_10-05-2026/
> __pycache__/
> .venv/
> ```

### Bước 2 — Build chroma_db và commit (hoặc dùng external storage)

**Cách đơn giản nhất**: build `chroma_db/` trên máy, rồi commit nó lên repo (nếu dữ liệu không nhạy cảm):

```bash
python ingest.py
git add chroma_db/
git commit -m "Add vector database"
git push
```

> Nếu `chroma_db/` quá lớn (>1 GB), dùng Git LFS hoặc chuyển sang lưu trữ ngoài.

### Bước 3 — Deploy trên Streamlit Community Cloud

1. Vào [share.streamlit.io](https://share.streamlit.io) → **New app**
2. Chọn repo GitHub, branch `main`, file `app.py`
3. Vào **Advanced settings → Secrets**, thêm:

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
APP_PASSWORD = "mật-khẩu-của-bạn"
ONEDRIVE_BASE_URL = "https://..."
```

4. Click **Deploy** — nhận URL dạng `https://yourapp.streamlit.app`
5. Chia sẻ URL này cho toàn bộ nhân viên.

---

## Cập nhật tài liệu

Mỗi khi thêm / sửa file trong `MANUAL_ROOT`:

```bash
python ingest.py          # upsert các chunk mới/thay đổi
# nếu dùng Streamlit Cloud, commit và push chroma_db/ lại
```

---

## Cấu trúc project

```
me-school-agent/
├── app.py              # Streamlit chat UI
├── ingest.py           # Script build vector database
├── retriever.py        # Tìm kiếm ChromaDB
├── prompts.py          # System prompts tiếng Việt
├── requirements.txt
├── .env.example
├── .gitignore
└── chroma_db/          # Vector database (tạo sau khi chạy ingest.py)
```
