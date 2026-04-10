"""
Database layer: SQLAlchemy models and CRUD helpers for
  - chat_history   (question, answer, sources)
  - indexing_jobs  (file lifecycle, PageIndex doc_id, status)
"""

import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
import os

from sqlalchemy import (
    Column, Text, DateTime, JSON, create_engine
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, sessionmaker

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

DATABASE_URL = os.environ["DATABASE_URL"]
# SQLAlchemy 1.4+ dropped the legacy "postgres://" scheme
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class ChatHistory(Base):
    __tablename__ = "chat_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    # [{"doc_name": "...", "doc_id": "...", "pages": "5-7"}, ...]
    sources = Column(JSON, default=list)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


class IndexingJob(Base):
    __tablename__ = "indexing_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename = Column(Text, nullable=False)
    file_path = Column(Text)
    # pending | processing | completed | failed
    status = Column(Text, default="pending", nullable=False)
    pageindex_doc_id = Column(Text)          # doc_id returned by PageIndex
    doc_description = Column(Text)           # description from PageIndex
    page_count = Column(Text)                # pageNum from PageIndex
    error_message = Column(Text)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


# ── CRUD helpers ──────────────────────────────────────────────────────────────

def save_chat(question: str, answer: str, sources: list) -> dict:
    db = SessionLocal()
    try:
        entry = ChatHistory(question=question, answer=answer, sources=sources)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return _chat_to_dict(entry)
    finally:
        db.close()


def list_chats(limit: int = 100) -> list[dict]:
    db = SessionLocal()
    try:
        rows = (
            db.query(ChatHistory)
            .order_by(ChatHistory.created_at.desc())
            .limit(limit)
            .all()
        )
        return [_chat_to_dict(r) for r in rows]
    finally:
        db.close()


def create_job(filename: str, file_path: str) -> dict:
    db = SessionLocal()
    try:
        job = IndexingJob(filename=filename, file_path=file_path, status="pending")
        db.add(job)
        db.commit()
        db.refresh(job)
        return _job_to_dict(job)
    finally:
        db.close()


def update_job(job_id: str, **kwargs) -> None:
    db = SessionLocal()
    try:
        job = db.query(IndexingJob).filter(IndexingJob.id == job_id).first()
        if job:
            for k, v in kwargs.items():
                setattr(job, k, v)
            job.updated_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()


def list_jobs() -> list[dict]:
    db = SessionLocal()
    try:
        rows = (
            db.query(IndexingJob)
            .order_by(IndexingJob.created_at.desc())
            .all()
        )
        return [_job_to_dict(r) for r in rows]
    finally:
        db.close()


def list_completed_jobs() -> list[dict]:
    db = SessionLocal()
    try:
        rows = (
            db.query(IndexingJob)
            .filter(
                IndexingJob.status == "completed",
                IndexingJob.pageindex_doc_id.isnot(None),
            )
            .all()
        )
        return [_job_to_dict(r) for r in rows]
    finally:
        db.close()


# ── private serialisers ───────────────────────────────────────────────────────

def _chat_to_dict(c: ChatHistory) -> dict:
    return {
        "id": str(c.id),
        "question": c.question,
        "answer": c.answer,
        "sources": c.sources or [],
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _job_to_dict(j: IndexingJob) -> dict:
    return {
        "id": str(j.id),
        "filename": j.filename,
        "file_path": j.file_path,
        "status": j.status,
        "pageindex_doc_id": j.pageindex_doc_id,
        "doc_description": j.doc_description,
        "page_count": j.page_count,
        "error_message": j.error_message,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "updated_at": j.updated_at.isoformat() if j.updated_at else None,
    }
