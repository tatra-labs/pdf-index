"""
Thin wrapper around the PageIndex cloud SDK (pageindex PyPI package v0.2+).

Real endpoints (from SDK source):
  POST  /doc/               — submit_document
  GET   /doc/{id}/metadata/ — get_document
  POST  /retrieval/         — submit_query (per-doc retrieval)
  GET   /retrieval/{id}/    — get_retrieval
  POST  /chat/completions/  — chat_completions (multi-doc supported)

Auth header: {"api_key": "<PAGEINDEX_API_KEY>"}   (NOT "Authorization: Bearer")

Citation streaming:
  chat_completions(stream=True, stream_metadata=True, enable_citations=True)
  yields raw SSE dicts; citation events have object="chat.completion.citations".
"""

import os
import time
from typing import Iterator

from dotenv import load_dotenv
from pageindex import PageIndexClient

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


def _client() -> PageIndexClient:
    api_key = os.environ.get("PAGEINDEX_API_KEY", "")
    if not api_key:
        raise RuntimeError("PAGEINDEX_API_KEY is not set")
    return PageIndexClient(api_key=api_key)


# ── Document lifecycle ─────────────────────────────────────────────────────────

def submit_document(file_path: str) -> str:
    """Upload a PDF and return its doc_id immediately (indexing runs async)."""
    result = _client().submit_document(file_path)
    return result["doc_id"]


def get_document(doc_id: str) -> dict:
    """
    Return document metadata.
    Keys: id, name, description, status, createdAt, pageNum
    Status values: queued | processing | completed | failed
    """
    return _client().get_document(doc_id)


def wait_for_ready(doc_id: str, poll_interval: int = 15, timeout: int = 600) -> dict:
    """
    Block until the document reaches 'completed' or 'failed'.
    Returns the final metadata dict.
    """
    deadline = time.time() + timeout
    client = _client()
    while time.time() < deadline:
        info = client.get_document(doc_id)
        if info.get("status") in ("completed", "failed"):
            return info
        time.sleep(poll_interval)
    raise TimeoutError(f"Document {doc_id} did not finish within {timeout}s")


# ── Chat / RAG ─────────────────────────────────────────────────────────────────

def chat_with_docs(
    question: str,
    doc_ids: list[str],
) -> tuple[str, list[dict]]:
    """
    Query one or more documents with a natural-language question.

    Uses:
      - stream=True + stream_metadata=True → raw SSE dicts
      - enable_citations=True             → citation events in stream

    Returns
    -------
    answer   : str          — clean answer text
    sources  : list[dict]   — [{"doc_name": str, "doc_id": str, "pages": str}, ...]
    """
    client = _client()

    stream: Iterator[dict] = client.chat_completions(
        messages=[{"role": "user", "content": question}],
        doc_id=doc_ids if len(doc_ids) > 1 else doc_ids[0],
        stream=True,
        stream_metadata=True,
        enable_citations=True,
    )

    answer_parts: list[str] = []
    sources: list[dict] = []
    seen: set[tuple] = set()

    for chunk in stream:
        obj = chunk.get("object", "")

        if obj == "chat.completion.chunk":
            content = (
                chunk.get("choices", [{}])[0]
                .get("delta", {})
                .get("content", "")
            )
            if content:
                answer_parts.append(content)

        elif obj == "chat.completion.citations":
            # Extract citation data — handle multiple possible shapes
            raw_cits = (
                chunk.get("citations")          # list form
                or chunk.get("data", {}).get("citations")
                or []
            )
            for c in raw_cits:
                doc_name = c.get("doc_name") or c.get("name", "")
                doc_id = c.get("doc_id") or c.get("id", "")
                pages = c.get("pages") or c.get("page_range", "")
                key = (doc_name, pages)
                if key not in seen:
                    seen.add(key)
                    sources.append(
                        {"doc_name": doc_name, "doc_id": doc_id, "pages": pages}
                    )

    return "".join(answer_parts), sources


# ── Retrieval (tree-search, no LLM answer) ────────────────────────────────────

def retrieve_from_doc(doc_id: str, query: str, poll_interval: int = 3, timeout: int = 60) -> dict:
    """
    Submit a retrieval query for one document; poll until complete.

    Returns the raw retrieval result dict (contains retrieved pages/content).
    Useful when you want raw content to feed into your own LLM synthesis step.
    """
    client = _client()
    result = client.submit_query(doc_id=doc_id, query=query)
    retrieval_id = result["retrieval_id"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get_retrieval(retrieval_id)
        if data.get("status") in ("completed", "failed"):
            return data
        time.sleep(poll_interval)

    raise TimeoutError(f"Retrieval {retrieval_id} did not finish within {timeout}s")
