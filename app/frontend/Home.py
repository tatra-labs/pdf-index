"""
Chat page — the main Streamlit page.

Layout
------
  ┌──────────────────────────────────────────────┐
  │  Investment Research Assistant               │
  │  ────────────────────────────────────────    │
  │  [assistant]  Welcome message                │
  │  [user]       User question                  │
  │  [assistant]  Answer                         │
  │               ── Sources ──                  │
  │               • Document A, pp. 3-5          │
  │               • Document B, pp. 12           │
  │  ──────────────────────────────────────────  │
  │  [ Ask a question about your documents… ] ↵  │
  └──────────────────────────────────────────────┘
"""

import httpx
import streamlit as st

API_BASE = "http://localhost:8000"

st.set_page_config(
    page_title="Investment Research Assistant",
    page_icon="📊",
    layout="wide",
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_CHAT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)


def _api_post(path: str, payload: dict) -> dict | None:
    try:
        r = httpx.post(f"{API_BASE}{path}", json=payload, timeout=_CHAT_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except httpx.ReadTimeout:
        st.error(
            "The request timed out (>5 min). "
            "PageIndex may still be processing — please try again in a moment."
        )
    except httpx.HTTPStatusError as e:
        st.error(f"API error {e.response.status_code}: {e.response.text}")
    except httpx.ConnectError:
        st.error("Cannot reach backend. Is the server running on port 8000?")
    return None


def _format_sources(sources: list[dict]) -> str:
    if not sources:
        return ""
    lines = []
    for s in sources:
        name = s.get("doc_name", "Unknown document")
        pages = s.get("pages")
        if pages:
            lines.append(f"- **{name}**, pp. {pages}")
        else:
            lines.append(f"- **{name}**")
    return "\n".join(lines)


# ── Session state ─────────────────────────────────────────────────────────────

if "messages" not in st.session_state:
    st.session_state.messages = []


# ── Header ────────────────────────────────────────────────────────────────────

st.title("📊 Investment Research Assistant")
st.caption(
    "Ask questions about your indexed investment documents. "
    "Answers are grounded in source material with page-level citations."
)

# Sidebar — quick doc status
with st.sidebar:
    st.header("Indexed Documents")
    try:
        docs_resp = httpx.get(f"{API_BASE}/api/documents", timeout=10)
        jobs = docs_resp.json() if docs_resp.status_code == 200 else []
        completed = [j for j in jobs if j["status"] == "completed"]
        processing = [j for j in jobs if j["status"] == "processing"]
        failed = [j for j in jobs if j["status"] == "failed"]

        if completed:
            for j in completed:
                pages = f" ({j['page_count']} pp.)" if j.get("page_count") else ""
                st.success(f"✓ {j['filename']}{pages}", icon=None)
        if processing:
            for j in processing:
                st.info(f"⟳ {j['filename']} (indexing…)")
        if failed:
            for j in failed:
                st.error(f"✗ {j['filename']}")
        if not jobs:
            st.info("No documents yet. Go to **Documents** to upload PDFs.")
    except Exception:
        st.warning("Could not fetch document list.")

    st.markdown("---")
    st.page_link("pages/1_Documents.py", label="Manage Documents", icon="📁")


# ── Chat history display ──────────────────────────────────────────────────────

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("sources"):
            with st.expander("📎 Sources", expanded=False):
                st.markdown(_format_sources(msg["sources"]))


# ── Chat input ────────────────────────────────────────────────────────────────

if prompt := st.chat_input("Ask a question about your documents…"):
    # Display user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Call backend
    with st.chat_message("assistant"):
        with st.spinner("Searching documents…"):
            result = _api_post("/api/chat", {"question": prompt})

        if result:
            answer = result.get("answer", "")
            sources = result.get("sources", [])

            st.markdown(answer)

            if sources:
                with st.expander("📎 Sources", expanded=True):
                    st.markdown(_format_sources(sources))

            st.session_state.messages.append(
                {"role": "assistant", "content": answer, "sources": sources}
            )
        else:
            err_msg = "Sorry, something went wrong. Please try again."
            st.markdown(err_msg)
            st.session_state.messages.append(
                {"role": "assistant", "content": err_msg}
            )
