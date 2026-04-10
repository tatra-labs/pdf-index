# Investment Research RAG System

A retrieval-augmented generation (RAG) system for investment documents.
Ask natural-language questions over multiple PDFs and receive grounded answers with page-level citations.

---

## Table of Contents

1. [System Architecture](#system-architecture)
2. [Database Design](#database-design)
3. [Retrieval Approach](#retrieval-approach)
4. [Handling Key Challenges](#handling-key-challenges)
5. [Design Decisions and Tradeoffs](#design-decisions-and-tradeoffs)
6. [Scalability Considerations](#scalability-considerations)
7. [Setup Instructions](#setup-instructions)
8. [Known Limitations](#known-limitations)
9. [Access Control](#access-control)
10. [What I Would Improve With More Time](#what-i-would-improve-with-more-time)

---

## System Architecture

```
┌─────────────────────┐      HTTP       ┌─────────────────────────┐
│   Streamlit (8501)  │ ◄─────────────► │    FastAPI (8000)        │
│                     │                 │                          │
│  • Chat page        │                 │  POST /api/chat          │
│  • Documents page   │                 │  GET  /api/chat/history  │
└─────────────────────┘                 │  POST /api/documents/    │
                                        │       ingest             │
                                        │  GET  /api/documents     │
                                        └────────────┬────────────┘
                                                     │
                              ┌──────────────────────┼──────────────────────┐
                              │                      │                       │
                     ┌────────▼────────┐  ┌─────────▼──────┐  ┌────────────▼───────┐
                     │  Supabase       │  │  PageIndex     │  │  Claude API        │
                     │  PostgreSQL     │  │  Cloud API     │  │  (Anthropic)       │
                     │                 │  │                │  │                    │
                     │  chat_history   │  │  submit_doc    │  │  Sonnet: synthesis │
                     │  indexing_jobs  │  │  get_doc       │  │    + attribution   │
                     └─────────────────┘  │  chat_compl.   │  └────────────────────┘
                                          └────────────────┘
```

### Component roles

| Component | Role |
|-----------|------|
| **Streamlit** | Two-page UI: chat interface + document management |
| **FastAPI** | REST API layer; background indexing workers; clean separation from UI |
| **PageIndex Cloud** | PDF → hierarchical tree index; reasoning-based retrieval per document |
| **Claude Sonnet** | Multi-document synthesis with version attribution and conflict surfacing |
| **Supabase PostgreSQL** | Persistent storage for chat history and indexing job state |

---

## Database Design

Two tables are created automatically on first server start.

### `chat_history`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | auto-generated |
| `question` | TEXT | user question |
| `answer` | TEXT | generated answer |
| `sources` | JSONB | `[{"doc_name", "doc_id", "pages"}, …]` |
| `created_at` | TIMESTAMPTZ | |

### `indexing_jobs`
| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | auto-generated |
| `filename` | TEXT | original PDF name |
| `file_path` | TEXT | local storage path |
| `status` | TEXT | `pending` → `processing` → `completed` \| `failed` |
| `pageindex_doc_id` | TEXT | `pi-xxx…` returned by PageIndex |
| `doc_description` | TEXT | auto-generated description from PageIndex |
| `page_count` | TEXT | total pages |
| `error_message` | TEXT | populated on failure |
| `created_at` / `updated_at` | TIMESTAMPTZ | |

**Why PostgreSQL over a vector DB?**  
This system deliberately has no vector database. PageIndex's tree-based index replaces it.
PostgreSQL only stores job state and chat history — lightweight structured data that relational
storage handles cleanly, with full ACID guarantees and easy querying.

---

## Retrieval Approach

### Why vectorless RAG?

Traditional vector-based RAG embeds text chunks and retrieves by cosine similarity.
The core problem: **similarity ≠ relevance**. A chunk that mentions "revenue" is similar
to every other chunk mentioning "revenue" — but only one section actually contains the
quarterly figure you need. For investment documents that demand domain precision, this
leads to noisy, unreliable retrieval.

**PageIndex** takes a different approach. It builds a **hierarchical tree index** of each PDF
— a semantic table of contents with section titles, summaries, and page ranges. At query
time, an LLM *reasons* over this tree to identify which sections are relevant, then fetches
only those pages. This is how a human analyst would navigate a 200-page report.

Benefits over vector search:
- **Traceable**: every retrieved chunk has a named section and page range
- **No chunking artifacts**: sections follow natural document boundaries
- **Higher precision** on domain-specific queries that require contextual reasoning

### Multi-document pipeline

```
user question
      │
      ▼
PageIndex chat_completions
  doc_id = [all completed doc_ids]     ← native multi-doc support
  stream_metadata = True
  enable_citations = True
      │
      ├─ text chunks   → answer text
      └─ citation chunks (object="chat.completion.citations") → sources list
      │
      ▼
[if multi-doc and no structured citations returned]
      │
      ▼
Claude Sonnet synthesis
  — explicit version attribution
  — conflict surfacing
  — chart/table limitation notes
      │
      ▼
{ answer: str, sources: [{ doc_name, doc_id, pages }] }
      │
      ▼
Persisted to chat_history (PostgreSQL)
```

**Fallback path**: if PageIndex returns an empty answer (edge case for unusual PDFs),
the system falls back to per-document `submit_query` / `get_retrieval` (PageIndex's
lower-level tree-search endpoint), feeds the retrieved text to Claude Sonnet for synthesis.

---

## Handling Key Challenges

### 1. Version awareness

When the same company uploads materials from different periods (e.g. a 2022 pitch deck
and a 2024 annual report), the system must not silently merge contradictory numbers.

**How it works:**
- Each document retains its original filename throughout the pipeline (used as the citation label).
- The Claude synthesis system prompt explicitly instructs:
  *"If documents describe different time periods or versions of the same company,
  treat each separately and note the difference."*
- Example output:
  > "Revenue was reported as $2.1B in [Company_2022_Deck.pdf, pp. 4], while
  > [Company_2024_Report.pdf, pp. 12] shows $3.8B — a 81% increase over two years."

### 2. Conflicting information

Conflicts arise when two documents state different values for the same metric, or when
a third-party report contradicts a company's own materials.

**How it works:**
- The synthesis prompt mandates surfacing conflicts rather than resolving them silently:
  *"If values or facts conflict across documents, surface the conflict explicitly
  rather than picking one silently."*
- Attribution is preserved at the individual claim level, not just at the document level.
- Example output:
  > "The company's own deck [Deck.pdf, pp. 8] cites a 40% market share.
  > The third-party report [MarketReport.pdf, pp. 23] puts this figure at 28%.
  > These figures may reflect different market definitions or measurement dates."

### 3. Charts, tables, and structured content

**What works:**
- Text-layer tables (PDF content stream) are extracted cleanly by PageIndex OCR.
- Numeric data stated in prose is captured reliably.
- Section headers and footnotes are preserved, which helps locate financial schedules.

**What doesn't work:**
- Chart images (bar charts, line graphs, pie charts rendered as raster images) are not
  extractable from the text layer.
- Tables rendered as images (scanned PDFs, image-only PDFs) are also missed.

**How we handle it:**
- The synthesis prompt tells Claude to note when visual content is referenced but
  data is unavailable: *"(chart data not extractable from PDF text layer)"*.
- This is surfaced explicitly to the user rather than hallucinated or silently omitted.
- For scanned PDFs, the recommendation is to pre-process with an OCR tool before ingestion.

---

## Design Decisions and Tradeoffs

### Why FastAPI + Streamlit (two servers) rather than Streamlit alone?

Streamlit can call Python functions directly without an HTTP layer, which is simpler.
I chose FastAPI as a separate service for three reasons:

1. **Separation of concerns**: the RAG logic is independently testable and callable
   from non-UI clients (scripts, notebooks, other services).
2. **Background tasks**: FastAPI's `BackgroundTasks` cleanly decouples PDF upload
   from the long-running indexing process without blocking the HTTP response.
3. **API-first design**: for a production system, the frontend may be replaced (mobile
   app, internal tool, Slack bot) without touching the backend.

The tradeoff is operational complexity — two processes to start. Mitigated by `run.sh` / `run.bat`.

### Why PageIndex over a vector DB?

See [Retrieval Approach](#retrieval-approach). The short version: for professional investment
documents where precision matters and answers must be traceable, reasoning-based retrieval
consistently outperforms similarity search. PageIndex achieved 98.7% on FinanceBench vs
~65–75% for typical vector RAG systems on the same benchmark.

### Why Claude Sonnet for synthesis rather than using PageIndex's built-in answer?

PageIndex's `chat_completions` already produces a good single-document answer.
For multi-document scenarios, I bring in Claude Sonnet because:
- I can inject a **custom system prompt** that enforces attribution rules, version-awareness
  instructions, and conflict-surfacing requirements specific to investment research.
- PageIndex's built-in multi-doc answer may silently merge conflicting facts;
  Claude's synthesis step makes the attribution and conflict handling explicit and auditable.

Tradeoff: adds latency and cost for multi-doc queries. Acceptable for a research tool
where accuracy matters more than speed.

### Why store indexing state in PostgreSQL rather than querying PageIndex directly?

PageIndex's `list_documents` endpoint could replace the `indexing_jobs` table,
but I chose to own this state locally because:
- It keeps the document's local filename (the PDF name the user uploaded), which is
  the human-readable citation label. PageIndex stores its internal name, which may differ.
- It decouples the app from PageIndex's API availability for status checks.
- It provides a natural place to add metadata (client_id, tags, upload timestamp)
  without touching PageIndex's data model.

### Chunking strategy

This system does **not chunk documents**. PageIndex's tree index serves the same role:
it divides documents into natural sections (chapters, sub-sections) rather than
fixed-size overlapping windows. This avoids the most common chunking failure modes:
- split sentences / incomplete context
- arbitrary boundary at a key table row
- overlap inflation that skews retrieval scores

---

## Scalability Considerations

The current system is a single-server proof of concept. Here is how each layer scales:

| Layer | Current | At Scale |
|-------|---------|----------|
| **Indexing** | Background thread per upload | Queue (Celery + Redis / SQS); multiple worker processes |
| **RAG queries** | Synchronous FastAPI handler | Async handlers + connection pooling; response streaming |
| **Database** | Single Supabase instance | Read replicas for chat history; connection pool (PgBouncer) |
| **Document storage** | Local disk | Object storage (S3 / GCS); presigned URLs |
| **Auth** | None | JWT middleware; per-tenant row-level security in PostgreSQL |
| **PageIndex** | Cloud API (managed) | Already horizontally scaled by provider |
| **Claude** | Direct API call | Batching or caching for repeated queries on same content |

**Bottleneck today:** the RAG query is synchronous and can take 30–120 seconds.
The immediate fix is streaming the response (FastAPI SSE + `st.write_stream` in Streamlit),
which lets the user read the answer as it arrives rather than waiting for the full response.

---

## Setup Instructions

### 1. Prerequisites

- Python 3.11+
- A free [Supabase](https://supabase.com) project (or any PostgreSQL instance)
- A [PageIndex API key](https://dash.pageindex.ai/api-keys)
- An [Anthropic API key](https://console.anthropic.com)

### 2. Environment variables

Edit `.env` in the project root (one level above this directory):

```bash
ANTHROPIC_API_KEY=sk-ant-...
PAGEINDEX_API_KEY=your_pageindex_key
DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

> **Note:** `DATABASE_URL` must use the `postgresql://` scheme. The legacy `postgres://`
> scheme is not supported by SQLAlchemy 2.x.

### 3. Install dependencies

```bash
cd app
pip install -r requirements.txt
```

### 4. Run

**Windows:**
```bat
run.bat
```

**Linux / macOS:**
```bash
bash run.sh
```

Or start each server manually in separate terminals:

```bash
# Terminal 1 — backend (from app/ directory)
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — frontend (from app/ directory)
streamlit run frontend/Home.py --server.port 8501
```

Open **http://localhost:8501** in your browser.

---

## Usage

1. Go to the **Documents** page (`http://localhost:8501/Documents`) and upload PDFs.
2. Indexing runs in the background — the page auto-refreshes until all documents are ready (1–5 min each).
3. Return to the **Chat** page and ask a question, or click one of the suggestion chips.
4. Each answer includes a **Sources** expander showing document name and page range.

---

## Known Limitations

| Limitation | Detail | Mitigation |
|------------|--------|------------|
| Chart/image extraction | Raster images in PDFs are not readable | Noted in answers; pre-process scanned PDFs with OCR |
| Indexing latency | 1–5 min per document via PageIndex cloud | Async; status shown with auto-refresh |
| Scanned PDFs | No text layer → poor extraction | Use OCR-pre-processed PDFs |
| Citation granularity | Page ranges, not sentence-level | Sufficient for investment research use case |
| Response latency | 30–120 s per query (LLM reasoning) | Acceptable for research; streaming would improve UX |
| No re-index / delete | Documents cannot be removed from the UI | Add `DELETE /api/documents/{id}` endpoint |
| No authentication | All documents visible to all users | See Access Control section below |
| Multi-doc synthesis cost | Claude Sonnet called on every multi-doc query | Cache common queries; use Haiku for simple ones |

---

## Access Control

The current system is single-tenant. To support multiple clients:

1. **Database**: Add `client_id UUID` to `indexing_jobs` and `chat_history`. All queries filter by `client_id`. Enforce with PostgreSQL row-level security policies.
2. **Auth**: Add JWT middleware to FastAPI (`python-jose`). Embed `client_id` in the token payload. Streamlit passes the token in the `Authorization` header.
3. **Document isolation**: Each client's PageIndex `doc_id`s are only accessible via their own `indexing_jobs` rows. The RAG pipeline reads `list_completed_jobs(client_id=...)` instead of all documents.
4. **PageIndex folders**: The PageIndex API supports `folder_id` on `submit_document`. Create one folder per client to namespace documents at the provider level as well.

This design adds access control without restructuring the existing code — only the `list_completed_jobs` query and the API auth middleware need changes.

---

## What I Would Improve With More Time

- **Streaming responses**: FastAPI SSE + `st.write_stream` so the answer streams to the user token-by-token instead of waiting 30–120 s for the full response
- **Parallel document retrieval**: replace the sequential per-doc loop with `asyncio.gather` to query all documents concurrently
- **Re-index / delete**: UI actions and `DELETE /api/documents/{id}` endpoint
- **Conflict detection summary**: a dedicated panel that auto-flags numerical contradictions across documents (e.g. same metric, different values)
- **Inline citations**: highlight the exact sentence in the answer that corresponds to each source, with click-to-page navigation
- **Query caching**: cache identical questions against the same document set (Redis) to avoid redundant LLM calls
- **Docker Compose**: single `docker compose up` to start PostgreSQL, FastAPI, and Streamlit
- **Evaluation harness**: a set of ground-truth Q&A pairs to measure retrieval precision/recall as the system evolves
