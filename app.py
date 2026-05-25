"""
ME School Manual — Staff Query Agent
Streamlit chat UI backed by ChromaDB + Claude.

Run:
    streamlit run app.py
"""

import json
import os
import subprocess
import sys
import anthropic
import streamlit as st
from dotenv import load_dotenv

from prompts import SYSTEM_PROMPT, CLARIFY_SYSTEM_PROMPT
from retriever import search, format_context, unique_sources

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────

def _secret(key: str, default: str = "") -> str:
    """Read from Streamlit secrets (cloud) or .env (local)."""
    try:
        return st.secrets[key]
    except Exception:
        return os.getenv(key, default)

APP_PASSWORD    = _secret("APP_PASSWORD")
ANTHROPIC_KEY   = _secret("ANTHROPIC_API_KEY")
MODEL           = "claude-sonnet-4-5"
MODEL_FAST      = "claude-haiku-4-5"   # faster + cheaper for clarification check
MAX_TOKENS      = 4096                  # enough for the longest structured answers

# ── Page setup ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="ME School — Trợ lý nội bộ",
    page_icon="🏫",
    layout="centered",
)

# Custom CSS — minimal, clean
st.markdown(
    """
    <style>
    .source-box {
        background: #f0f4ff;
        border-left: 3px solid #4a6cf7;
        padding: 8px 12px;
        border-radius: 4px;
        font-size: 0.85rem;
        margin-top: 8px;
    }
    .source-box a { color: #4a6cf7; text-decoration: none; }
    .source-box a:hover { text-decoration: underline; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Password gate ──────────────────────────────────────────────────────────

def check_password() -> bool:
    if not APP_PASSWORD:
        return True  # no password set → open access

    if st.session_state.get("authenticated"):
        return True

    st.title("🏫 ME School — Trợ lý nội bộ")
    pwd = st.text_input("Nhập mật khẩu để tiếp tục:", type="password")
    if st.button("Đăng nhập"):
        if pwd == APP_PASSWORD:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Mật khẩu không đúng.")
    return False


if not check_password():
    st.stop()

# ── Anthropic client ───────────────────────────────────────────────────────

@st.cache_resource
def get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=ANTHROPIC_KEY)


# ── Clarification check ────────────────────────────────────────────────────

def needs_clarification(query: str) -> tuple[bool, list[str]]:
    """
    Ask Claude (with CLARIFY_SYSTEM_PROMPT) whether the query needs
    clarifying questions.  Returns (bool, list_of_questions).
    """
    client = get_client()
    try:
        resp = client.messages.create(
            model=MODEL_FAST,   # Haiku: ~5x faster for simple classification
            max_tokens=128,
            system=CLARIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": query}],
        )
        raw = resp.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        return data.get("needs_clarification", False), data.get("questions", [])
    except Exception:
        return False, []


# ── Retrieval ──────────────────────────────────────────────────────────────

def get_retrieval(conversation: list[dict]) -> tuple[str, list[dict]]:
    """
    Retrieve relevant chunks for the latest user message.
    Returns (context_string, source_list).
    """
    last_user_msg = next(
        (m["content"] for m in reversed(conversation) if m["role"] == "user"),
        "",
    )

    hits    = search(last_user_msg)
    context = format_context(hits)
    sources = unique_sources(hits)

    # If nothing found, tell Claude explicitly so it doesn't hallucinate
    if not hits:
        context = "Không tìm thấy tài liệu nào liên quan đến câu hỏi này."

    return context, sources


# ── Answer streaming ───────────────────────────────────────────────────────

def stream_answer(conversation: list[dict], context: str):
    """
    Generator that streams Claude's answer token-by-token.
    Call get_retrieval() first to obtain context.
    """
    system_with_context = (
        SYSTEM_PROMPT
        + f"\n\n[CONTEXT]\n{context}\n[/CONTEXT]"
    )

    client = get_client()
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_with_context,
        messages=conversation,
    ) as stream:
        for text in stream.text_stream:
            yield text


# ── Session state ──────────────────────────────────────────────────────────

if "messages" not in st.session_state:
    st.session_state.messages = []          # list of {role, content}

if "pending_clarification" not in st.session_state:
    st.session_state.pending_clarification = False   # waiting for user answer

if "original_query" not in st.session_state:
    st.session_state.original_query = ""

# ── UI ─────────────────────────────────────────────────────────────────────

st.title("🏫 ME School — Trợ lý nội bộ")
st.caption(
    "Hỏi bất kỳ điều gì về quy trình, biểu mẫu hoặc chính sách của ME School. "
    "Tôi chỉ trả lời dựa trên bộ tài liệu vận hành của trường."
)
st.divider()

# --- Render conversation history ---
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"], unsafe_allow_html=True)

# --- Chat input ---
user_input = st.chat_input("Nhập câu hỏi của bạn…")

if user_input:
    # Always show the user's message immediately
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # If we were waiting for the user to answer clarifying questions,
    # the new input IS that answer — go straight to answering.
    skip_clarify = st.session_state.pending_clarification
    st.session_state.pending_clarification = False

    if not skip_clarify:
        # Check whether the query needs clarification
        with st.spinner("Đang kiểm tra câu hỏi…"):
            clarify, questions = needs_clarification(user_input)

        if clarify and questions:
            # Build the clarifying message
            clarify_text = "Để tôi có thể tìm đúng tài liệu cho bạn, xin hỏi thêm:\n\n"
            clarify_text += f"• {questions[0]}\n"

            st.session_state.messages.append(
                {"role": "assistant", "content": clarify_text}
            )
            st.session_state.pending_clarification = True
            st.session_state.original_query = user_input

            with st.chat_message("assistant"):
                st.markdown(clarify_text)

            st.stop()

    # --- Retrieve relevant docs (fast — shown under spinner) ---
    with st.spinner("Đang tìm kiếm tài liệu…"):
        try:
            context, sources = get_retrieval(st.session_state.messages)
        except Exception as e:
            context = "Không tìm thấy tài liệu nào liên quan đến câu hỏi này."
            sources = []

    # Build source footer HTML
    source_html = ""
    if sources:
        links = "".join(
            f'• <a href="{s["url"]}" target="_blank">{s["file_name"]}</a><br>'
            for s in sources
            if s.get("url")
        )
        if links:
            source_html = (
                '<div class="source-box">📄 <strong>Nguồn:</strong><br>'
                + links
                + "</div>"
            )

    # --- Stream the answer — user sees text within ~2 s ---
    with st.chat_message("assistant"):
        try:
            answer = st.write_stream(stream_answer(st.session_state.messages, context))
        except Exception as e:
            answer = f"⚠️ Lỗi khi gọi API: {e}"
            st.markdown(answer)

        if source_html:
            st.markdown(source_html, unsafe_allow_html=True)

    full_response = answer + ("\n\n" + source_html if source_html else "")
    st.session_state.messages.append(
        {"role": "assistant", "content": full_response}
    )

# --- Sidebar ---
with st.sidebar:
    st.header("Về trợ lý này")
    st.markdown(
        """
        **ME School Internal Assistant**

        Trợ lý chỉ trả lời dựa trên tài liệu vận hành nội bộ của ME School.
        Mọi câu trả lời đều có kèm đường dẫn đến file gốc.

        ---
        **Cách sử dụng:**
        1. Nhập câu hỏi vào ô chat
        2. Trợ lý có thể hỏi thêm để làm rõ
        3. Nhận câu trả lời kèm nguồn tài liệu
        """
    )

    st.divider()
    st.subheader("🗄️ Cập nhật tài liệu")

    # Check if running locally (MANUAL_ROOT exists) or on Streamlit Cloud
    manual_root = os.getenv("MANUAL_ROOT", "")
    is_local = manual_root and os.path.exists(manual_root)

    if is_local:
        st.caption("Dùng khi thêm hoặc sửa file trong thư mục tài liệu.")
        if st.button("🔄 Cập nhật tài liệu", use_container_width=True):
            with st.spinner("Đang cập nhật… (vài phút)"):
                result = subprocess.run(
                    [sys.executable, "ingest.py"],
                    capture_output=True, text=True
                )
            if result.returncode == 0:
                st.success("✅ Cập nhật xong!")
            else:
                st.error("❌ Lỗi:\n" + result.stderr[-500:])

        st.caption("Dùng khi XOÁ file khỏi thư mục tài liệu.")
        if st.button("🔁 Xây lại toàn bộ", use_container_width=True):
            with st.spinner("Đang xây lại từ đầu… (5-10 phút)"):
                result = subprocess.run(
                    [sys.executable, "ingest.py", "--reset"],
                    capture_output=True, text=True
                )
            if result.returncode == 0:
                st.success("✅ Xây lại xong!")
            else:
                st.error("❌ Lỗi:\n" + result.stderr[-500:])
    else:
        st.info(
            "Để cập nhật tài liệu: chạy `2_build_database.sh` trên máy tính "
            "→ commit & push lên GitHub → app tự cập nhật sau 1–2 phút."
        )

    st.divider()
    if st.button("🗑️ Xoá lịch sử chat", use_container_width=True):
        st.session_state.messages = []
        st.session_state.pending_clarification = False
        st.rerun()
