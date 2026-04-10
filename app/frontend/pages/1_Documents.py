"""
Document Management page.

Features
--------
- Upload one or multiple PDFs at once
- Status table: filename | status badge | pages | description | uploaded at
- Auto-refresh while any document is still processing
- Manual refresh button
"""

import time

import httpx
import streamlit as st

API_BASE = "http://localhost:8000"

st.set_page_config(
    page_title="Document Management",
    page_icon="📁",
    layout="wide",
)

# ── Helpers ───────────────────────────────────────────────────────────────────

STATUS_ICONS = {
    "pending":    "🕐 Pending",
    "processing": "⟳ Processing",
    "completed":  "✅ Ready",
    "failed":     "❌ Failed",
}

STATUS_COLORS = {
    "pending":    "orange",
    "processing": "blue",
    "completed":  "green",
    "failed":     "red",
}


def _get_jobs() -> list[dict]:
    try:
        r = httpx.get(f"{API_BASE}/api/documents", timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        st.error(f"Could not load documents: {e}")
        return []


def _upload_file(file) -> dict | None:
    try:
        r = httpx.post(
            f"{API_BASE}/api/documents/ingest",
            files={"file": (file.name, file.getvalue(), "application/pdf")},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        st.error(f"Upload failed ({e.response.status_code}): {e.response.text}")
    except httpx.ConnectError:
        st.error("Cannot reach backend. Is the server running on port 8000?")
    return None


# ── Page header ───────────────────────────────────────────────────────────────

st.title("📁 Document Management")
st.caption(
    "Upload investment-related PDFs here. Each document is indexed by PageIndex "
    "in the background — indexing typically takes 1–5 minutes per document."
)

# ── Upload panel ──────────────────────────────────────────────────────────────

with st.expander("⬆️  Upload new PDFs", expanded=True):
    uploaded_files = st.file_uploader(
        "Select one or more PDF files",
        type=["pdf"],
        accept_multiple_files=True,
        help="Investor presentations, strategy decks, third-party reports — any PDF.",
    )

    if uploaded_files:
        if st.button("🚀  Start Indexing", type="primary"):
            for uf in uploaded_files:
                with st.spinner(f"Submitting {uf.name}…"):
                    result = _upload_file(uf)
                    if result:
                        st.success(
                            f"**{uf.name}** submitted (job id: {result['job_id'][:8]}…). "
                            "Indexing has started in the background."
                        )
            st.rerun()

st.markdown("---")

# ── Document table ────────────────────────────────────────────────────────────

col_left, col_right = st.columns([6, 1])
with col_left:
    st.subheader("All Documents")
with col_right:
    if st.button("🔄 Refresh"):
        st.rerun()

jobs = _get_jobs()

if not jobs:
    st.info("No documents yet. Upload PDFs above to get started.")
else:
    # Summary metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total", len(jobs))
    m2.metric("Ready",      sum(1 for j in jobs if j["status"] == "completed"))
    m3.metric("Processing", sum(1 for j in jobs if j["status"] in ("pending", "processing")))
    m4.metric("Failed",     sum(1 for j in jobs if j["status"] == "failed"))

    st.markdown("---")

    for job in jobs:
        status = job["status"]
        icon_label = STATUS_ICONS.get(status, status)
        color = STATUS_COLORS.get(status, "gray")

        with st.container(border=True):
            c1, c2, c3 = st.columns([4, 2, 4])

            with c1:
                st.markdown(f"**{job['filename']}**")
                if job.get("doc_description"):
                    st.caption(job["doc_description"][:160] + ("…" if len(job.get("doc_description","")) > 160 else ""))

            with c2:
                st.markdown(
                    f"<span style='color:{color}; font-weight:bold'>{icon_label}</span>",
                    unsafe_allow_html=True,
                )
                if job.get("page_count"):
                    st.caption(f"{job['page_count']} pages")

            with c3:
                if job.get("error_message"):
                    st.error(job["error_message"], icon="⚠️")
                if job.get("created_at"):
                    ts = job["created_at"][:19].replace("T", " ")
                    st.caption(f"Uploaded: {ts}")
                if job.get("pageindex_doc_id"):
                    st.caption(f"Doc ID: `{job['pageindex_doc_id']}`")

    # Auto-refresh while any document is processing
    if any(j["status"] in ("pending", "processing") for j in jobs):
        st.info("⟳ Documents are still indexing. Page will refresh automatically…")
        time.sleep(10)
        st.rerun()
