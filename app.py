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

from prompts import SYSTEM_PROMPT, INTERACTIVE_SYSTEM_PROMPT, CLARIFY_SYSTEM_PROMPT, ONBOARDING_QUERY, LEARNING_SIGNALS
from retriever import search, search_toc, has_toc, format_context, unique_sources

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
    page_title="Em gái Sotana — ME School",
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

    st.title("Em gái Sotana 🏫")
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
    Ask Claude (Haiku) if the query is ambiguous enough to warrant clarification.
    Only called when search quality is already confirmed poor.
    Returns (bool, list_of_questions).
    """
    client = get_client()
    try:
        resp = client.messages.create(
            model=MODEL_FAST,
            max_tokens=200,   # enough for JSON + one question
            system=CLARIFY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": query}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        return data.get("needs_clarification", False), data.get("questions", [])
    except Exception:
        return False, []


def _search_is_good(hits: list[dict]) -> bool:
    """
    Returns True if search results are strong enough to answer directly
    without asking for clarification.
    Strong = at least 1 keyword hit, OR 3+ semantic hits.
    """
    if not hits:
        return False
    keyword_hits = [h for h in hits if h.get("score", 1.0) == 0.0]
    if keyword_hits:
        return True       # filename/text keyword match → always good
    return len(hits) >= 3  # 3+ semantic hits → acceptable


# ── Learning mode detection ───────────────────────────────────────────────

MAX_HISTORY_TURNS = 5   # keep last N user-assistant pairs to control token growth

def _is_learning_query(query: str) -> bool:
    """Detect if a query signals desire to learn/understand (not just lookup).
    Uses keyword matching — no API call, zero cost."""
    q = query.lower()
    # Short factual lookups (≤3 words) are almost never learning queries
    if len(query.split()) <= 3:
        return False
    return any(signal in q for signal in LEARNING_SIGNALS)


def _trim_history(messages: list[dict]) -> list[dict]:
    """Keep only the last MAX_HISTORY_TURNS user-assistant pairs.
    Prevents token explosion in multi-turn interactive sessions."""
    if len(messages) <= MAX_HISTORY_TURNS * 2:
        return messages
    return messages[-(MAX_HISTORY_TURNS * 2):]


def _is_followup_on_same_topic(messages: list[dict]) -> bool:
    """Check if user is continuing the same topic (no need to re-retrieve).
    True when the latest user message is short and there's cached context."""
    if len(messages) < 3:  # need at least 1 prior exchange
        return False
    last_msg = messages[-1]["content"] if messages[-1]["role"] == "user" else ""
    # Short follow-ups (≤8 words) on existing conversation = same topic
    if len(last_msg.split()) <= 8 and st.session_state.get("_cached_context"):
        return True
    return False


# ── Retrieval ──────────────────────────────────────────────────────────────

def get_retrieval(conversation: list[dict], extra_query: str = "") -> tuple[str, list[dict], list[dict]]:
    """
    Retrieve relevant chunks for the latest user message.
    Strategy: hybrid search + TOC search (always), merge & deduplicate.
    extra_query: prepend original query when this is a clarification follow-up.
    Returns (context_string, source_list, raw_hits).
    """
    last_user_msg = next(
        (m["content"] for m in reversed(conversation) if m["role"] == "user"),
        "",
    )
    search_query = (extra_query + " " + last_user_msg).strip() if extra_query else last_user_msg

    # Step 1: Hybrid search (fast, no API call)
    hits = search(search_query)

    # Step 2: Always run TOC search alongside (uses Haiku API)
    # TOC search understands document meaning, not just keyword overlap
    if has_toc():
        toc_hits = search_toc(search_query, api_key=ANTHROPIC_KEY)
        if toc_hits:
            # Merge: add TOC results that aren't already in keyword/cosine hits
            existing_paths = {h.get("file_path", "") for h in hits}
            for th in toc_hits:
                if th.get("file_path", "") not in existing_paths:
                    hits.append(th)
                    existing_paths.add(th.get("file_path", ""))

    context = format_context(hits)
    sources = unique_sources(hits)

    if not hits:
        context = "Không tìm thấy tài liệu nào liên quan đến câu hỏi này."

    return context, sources, hits


# ── Answer streaming ───────────────────────────────────────────────────────

def stream_answer(conversation: list[dict], context: str,
                   interactive: bool = False):
    """
    Generator that streams Claude's answer token-by-token.
    Call get_retrieval() first to obtain context.
    interactive=True uses the learning/Socratic prompt.
    """
    base_prompt = INTERACTIVE_SYSTEM_PROMPT if interactive else SYSTEM_PROMPT
    system_with_context = (
        base_prompt
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

if "learning_mode" not in st.session_state:
    st.session_state.learning_mode = False  # toggle: learning vs quick-answer

if "_cached_context" not in st.session_state:
    st.session_state._cached_context = ""   # reuse context for follow-ups

if "_cached_sources" not in st.session_state:
    st.session_state._cached_sources = []

# ── UI ─────────────────────────────────────────────────────────────────────

st.title("Em gái Sotana 🏫")
if st.session_state.learning_mode:
    st.caption(
        "🎓 **Chế độ Học tập** — Tôi sẽ hướng dẫn bạn hiểu sâu tài liệu, "
        "đặt câu hỏi giúp bạn suy nghĩ, và gợi ý nội dung liên quan."
    )
else:
    st.caption(
        "Hỏi bất kỳ điều gì về quy trình, biểu mẫu hoặc chính sách của ME School. "
        "Tôi chỉ trả lời dựa trên bộ tài liệu vận hành của trường."
    )
st.divider()

# --- Render conversation history ---
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])  # clean text only — no HTML stored in history

# --- Chat input (or injected query from sidebar buttons) ---
user_input = st.chat_input("Nhập câu hỏi của bạn…")

# Sidebar buttons can inject a pre-written query; pick it up here
if not user_input and st.session_state.get("_inject_query"):
    user_input = st.session_state.pop("_inject_query")

if user_input:
    # Always show the user's message immediately
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # Was this a reply to a clarifying question?
    was_clarifying = st.session_state.pending_clarification
    original_q     = st.session_state.original_query if was_clarifying else ""
    st.session_state.pending_clarification = False
    st.session_state.original_query        = ""

    # Sidebar-injected queries (e.g. onboarding button) skip clarification entirely
    is_injected = (user_input == ONBOARDING_QUERY)

    # ── Determine mode: learning or quick-answer ───────────────────────────
    # Manual toggle overrides auto-detect; auto-detect kicks in when toggle is off
    use_interactive = st.session_state.learning_mode or (
        not is_injected and _is_learning_query(user_input)
    )

    # ── Step 1: Search (or reuse cached context for follow-ups) ────────────
    if use_interactive and _is_followup_on_same_topic(st.session_state.messages):
        # Reuse previous context — save a Haiku TOC call
        context = st.session_state._cached_context
        sources = st.session_state._cached_sources
        hits    = []  # not needed for follow-ups
    else:
        with st.spinner("Đang tìm kiếm tài liệu…"):
            try:
                context, sources, hits = get_retrieval(
                    st.session_state.messages,
                    extra_query=original_q,
                )
            except Exception as e:
                context = "Không tìm thấy tài liệu nào liên quan đến câu hỏi này."
                sources = []
                hits    = []
        # Cache for potential follow-ups
        st.session_state._cached_context = context
        st.session_state._cached_sources = sources

    # ── Step 2: Clarify only if search is weak AND query is short/ambiguous ─
    should_try_clarify = (
        not is_injected
        and not was_clarifying
        and not use_interactive        # interactive mode handles ambiguity itself
        and not _search_is_good(hits)
        and len(user_input.split()) <= 4
    )

    if should_try_clarify:
        clarify, questions = needs_clarification(user_input)
        if clarify and questions:
            clarify_text = f"👉 {questions[0]}"
            st.session_state.messages.append({"role": "assistant", "content": clarify_text})
            st.session_state.pending_clarification = True
            st.session_state.original_query        = user_input
            with st.chat_message("assistant"):
                st.markdown(clarify_text)
            st.stop()

    # ── Step 3: Build source footer HTML ────────────────────────────────────
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

    # ── Step 4: Stream the answer ────────────────────────────────────────────
    # Trim history to prevent token explosion in interactive sessions
    trimmed_messages = _trim_history(st.session_state.messages)

    with st.chat_message("assistant"):
        try:
            answer = st.write_stream(
                stream_answer(trimmed_messages, context,
                              interactive=use_interactive)
            )
        except Exception as e:
            answer = f"⚠️ Lỗi khi gọi API: {e}"
            st.markdown(answer)

        if source_html:
            st.markdown(source_html, unsafe_allow_html=True)

    # Store ONLY the clean answer text — never the source HTML.
    # Storing HTML causes Claude to echo the raw tags in future answers.
    st.session_state.messages.append(
        {"role": "assistant", "content": answer}
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
    st.subheader("🎓 Chế độ Học tập")
    st.caption(
        "Bật để Sotana tương tác như mentor — đặt câu hỏi, "
        "hướng dẫn suy nghĩ, gợi ý đọc thêm."
    )
    learning_on = st.toggle(
        "Bật chế độ học tập",
        value=st.session_state.learning_mode,
        key="learning_toggle",
    )
    if learning_on != st.session_state.learning_mode:
        st.session_state.learning_mode = learning_on
        st.rerun()

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
            "Tài liệu được cập nhật tự động qua GitHub Actions khi có file mới "
            "trên SharePoint. App tự cập nhật sau 1–2 phút."
        )

    st.divider()
    st.subheader("🎓 Onboarding nhân viên mới")
    st.caption("Tạo kế hoạch tự học 5 ngày từ toàn bộ tài liệu của trường.")
    if st.button("📋 Tạo kế hoạch onboarding", use_container_width=True):
        st.session_state.messages = []
        st.session_state.pending_clarification = False
        st.session_state["_inject_query"] = ONBOARDING_QUERY
        st.rerun()

    st.divider()
    if st.button("🗑️ Xoá lịch sử chat", use_container_width=True):
        st.session_state.messages = []
        st.session_state.pending_clarification = False
        st.session_state._cached_context = ""
        st.session_state._cached_sources = []
        st.rerun()
