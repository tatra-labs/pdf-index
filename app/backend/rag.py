"""
Multi-document RAG pipeline.

Flow
----
1. Retrieve all completed indexing jobs from the DB.
2. Call PageIndex chat_completions with ALL doc_ids in one request
   (the API natively supports a list of doc_ids).
   stream_metadata=True + enable_citations=True gives us structured
   citation events alongside text chunks.
3. If citations come back empty (API may not always populate them),
   fall back to a Claude Sonnet synthesis call over per-doc retrieval
   results — this also handles version-awareness and conflict detection.

Design notes
------------
* Version awareness — Claude synthesis prompt treats each document
  as an independent source and flags when the same metric differs
  across versions.
* Conflict detection — synthesis prompt surfaces contradictions
  explicitly rather than silently merging them.
* Charts / tables — PageIndex extracts the PDF text layer; chart
  images are not available. The prompt instructs Claude to note
  this limitation where relevant.
"""

import os

import anthropic
from dotenv import load_dotenv

from .database import list_completed_jobs
from .pageindex_client import chat_with_docs, retrieve_from_doc

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

_anthropic = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

_SYNTHESIS_SYSTEM = """\
You are an investment research analyst synthesising information from multiple
source documents to answer an investor's question.

Rules:
- Always attribute claims to a specific document using the format
  [Document Name] or [Document Name, pp. X-Y].
- If documents describe different time periods or versions of the same company,
  treat each separately and note the difference explicitly
  (e.g. "The 2022 deck reported ARR of $45M [Deck 2022, pp. 8], while the
  2024 update reported $72M [Deck 2024, pp. 5]").
- If values or facts conflict across documents, surface the conflict
  rather than silently picking one.
- If a document excerpt contains no useful answer, omit it — do not hallucinate.
- When a chart or figure is mentioned but numerical data is unavailable,
  note: "(chart data not extractable from PDF text layer)".
- Be concise and factual.
"""


# ── Public entry point ────────────────────────────────────────────────────────

def answer_question(question: str) -> dict:
    """
    Full multi-document RAG pipeline.

    Returns
    -------
    {
        "answer":  str,
        "sources": [{"doc_name": str, "doc_id": str, "pages": str}, ...]
    }
    """
    completed = list_completed_jobs()

    if not completed:
        return {
            "answer": (
                "No documents have been indexed yet. "
                "Please upload PDFs on the Documents page."
            ),
            "sources": [],
        }

    doc_ids = [d["pageindex_doc_id"] for d in completed if d.get("pageindex_doc_id")]

    # ── Primary path: PageIndex native multi-doc chat ──────────────────────────
    answer, sources = chat_with_docs(question, doc_ids)

    # ── Fallback: if PageIndex returned an answer but no structured citations,
    #    enrich with a Claude synthesis that adds attribution + conflict notes.
    if answer and len(completed) > 1 and not sources:
        answer = _enrich_with_claude(question, answer, completed)

    # ── If PageIndex returned nothing, fall back to retrieval + Claude synthesis
    if not answer.strip():
        answer, sources = _retrieval_then_synthesise(question, completed)

    return {
        "answer": answer or "No relevant information found in the indexed documents.",
        "sources": _dedup(sources),
    }


# ── Fallback: per-doc retrieval + Claude synthesis ────────────────────────────

def _retrieval_then_synthesise(question: str, docs: list[dict]) -> tuple[str, list[dict]]:
    """
    For each document, call PageIndex's tree-search retrieval endpoint
    (no LLM answer — just retrieved content), then feed everything to
    Claude Sonnet for a version-aware, conflict-surfacing synthesis.
    """
    excerpts: list[dict] = []
    sources: list[dict] = []

    for doc in docs:
        doc_id = doc.get("pageindex_doc_id")
        if not doc_id:
            continue
        try:
            result = retrieve_from_doc(doc_id, question)
            if result.get("status") != "completed":
                continue
            content = _extract_retrieval_content(result)
            if not content:
                continue
            excerpts.append({"doc_name": doc["filename"], "doc_id": doc_id, "content": content})
            # Build sources from retrieved pages if available
            pages = _extract_retrieval_pages(result)
            if pages:
                sources.append({"doc_name": doc["filename"], "doc_id": doc_id, "pages": pages})
        except Exception:
            continue

    if not excerpts:
        return "", []

    context = "\n\n".join(
        f"=== Source: {e['doc_name']} ===\n{e['content']}" for e in excerpts
    )

    response = _anthropic.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=_SYNTHESIS_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n\n"
                    f"Document excerpts:\n{context}\n\n"
                    "Provide a synthesised, well-attributed answer."
                ),
            }
        ],
    )

    return response.content[0].text, sources


def _enrich_with_claude(question: str, raw_answer: str, docs: list[dict]) -> str:
    """
    Post-process a multi-doc PageIndex answer with Claude to add
    explicit version/conflict notes and attribution.
    """
    doc_list = ", ".join(f'"{d["filename"]}"' for d in docs)

    response = _anthropic.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=_SYNTHESIS_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"The following answer was generated from these documents: {doc_list}\n\n"
                    f"Question: {question}\n\n"
                    f"Draft answer:\n{raw_answer}\n\n"
                    "Rewrite the answer to clearly attribute each claim to its source document, "
                    "flag any version differences, and highlight conflicts. "
                    "Keep it concise."
                ),
            }
        ],
    )

    return response.content[0].text


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_retrieval_content(result: dict) -> str:
    """Pull text content from a retrieval result dict."""
    # PageIndex retrieval result may have various shapes; try common keys
    for key in ("content", "text", "result", "answer"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, list):
            return "\n".join(
                item.get("content") or item.get("text") or ""
                for item in val
                if isinstance(item, dict)
            )
    return ""


def _extract_retrieval_pages(result: dict) -> str:
    """Try to extract page range info from a retrieval result."""
    pages = result.get("pages") or result.get("page_range")
    if pages:
        return str(pages)
    items = result.get("result") or result.get("content") or []
    if isinstance(items, list) and items:
        nums = [str(i.get("page") or i.get("page_num", "")) for i in items if isinstance(i, dict)]
        nums = [n for n in nums if n]
        if nums:
            return ", ".join(sorted(set(nums), key=lambda x: int(x) if x.isdigit() else 0))
    return ""


def _dedup(sources: list[dict]) -> list[dict]:
    seen: set[tuple] = set()
    out = []
    for s in sources:
        key = (s.get("doc_name", ""), s.get("pages", ""))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out
