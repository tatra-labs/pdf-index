"""
FastAPI backend — four REST endpoints:

  POST /api/chat                → run RAG, return answer + sources
  GET  /api/chat/history        → list past Q&A pairs (newest first)
  POST /api/documents/ingest    → upload + kick off PageIndex indexing
  GET  /api/documents           → list all jobs and their status

Background ingestion worker submits the PDF to PageIndex and polls until
the status reaches 'completed' or 'failed', updating the DB throughout.
"""

import os
import shutil
import time
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

# Import after env is loaded
from .database import (
    create_tables,
    create_job,
    list_chats,
    list_jobs,
    save_chat,
    update_job,
)
from .pageindex_client import get_document, submit_document, wait_for_ready
from .rag import answer_question

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="Investment RAG API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(__file__).parent.parent / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

POLL_INTERVAL = 15   # seconds between PageIndex status polls
POLL_TIMEOUT  = 600  # give up after 10 minutes


@app.on_event("startup")
def _startup():
    create_tables()


# ── Schemas ───────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/api/chat")
def chat(req: ChatRequest):
    """
    Run the multi-document RAG pipeline and persist the Q&A to the DB.
    """
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question must not be empty.")

    result = answer_question(req.question)
    save_chat(req.question, result["answer"], result["sources"])
    return result


@app.get("/api/chat/history")
def chat_history():
    """Return all past Q&A pairs, newest first."""
    return list_chats()


@app.post("/api/documents/ingest")
async def ingest_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """
    Accept a PDF upload, save it, create a DB job record, and start
    the background indexing task.  Returns immediately with the job ID.
    """
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    dest = UPLOAD_DIR / file.filename
    with open(dest, "wb") as fh:
        shutil.copyfileobj(file.file, fh)

    job = create_job(filename=file.filename, file_path=str(dest))
    background_tasks.add_task(_index_document, job["id"], str(dest))

    return {"job_id": job["id"], "filename": file.filename, "status": "pending"}


@app.get("/api/documents")
def list_documents():
    """List all indexing jobs and their current status."""
    return list_jobs()


# ── Background worker ─────────────────────────────────────────────────────────

def _index_document(job_id: str, file_path: str) -> None:
    """
    1. Submit the PDF to PageIndex cloud API.
    2. Poll until status is 'completed' or 'failed'.
    3. Update the DB record throughout.
    """
    try:
        update_job(job_id, status="processing")
        doc_id = submit_document(file_path)
        update_job(job_id, status="processing", pageindex_doc_id=doc_id)

        info = wait_for_ready(doc_id, poll_interval=POLL_INTERVAL, timeout=POLL_TIMEOUT)

        if info.get("status") == "completed":
            update_job(
                job_id,
                status="completed",
                doc_description=info.get("description", ""),
                page_count=str(info.get("pageNum", "")),
            )
        else:
            update_job(job_id, status="failed", error_message="PageIndex reported failure.")

    except TimeoutError as exc:
        update_job(job_id, status="failed", error_message=str(exc))
    except Exception as exc:
        update_job(job_id, status="failed", error_message=str(exc))


# ── Dev server entry-point ────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
