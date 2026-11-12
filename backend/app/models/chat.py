"""Conversations, messages and saved questions (BUILD_SPEC 5, X2; M8).

* A **conversation** belongs to one person (history is per user, X2) and has a lens (Business, Marketing, Finance).
* A **message** is either the person's question (``role = user``) or the agent's answer (``role = assistant``).
  An answer is made by a background job: it starts ``queued``, becomes ``running`` (``progress_json`` lists the
  steps as they happen, for the Ask screen) and ends ``done`` or ``failed``. The finished answer keeps its
  structured content (``answer_json``), the tool trace ("Show my work"), provenance of every result, data-quality
  warnings and token usage. Customer data rows are never stored here beyond the few rows a result shows.
* A **saved question** is a question a person can run again with one click.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import IdMixin, TimestampMixin

Lens = Literal["business", "marketing", "finance"]
LENSES: tuple[Lens, ...] = ("business", "marketing", "finance")
MessageRole = Literal["user", "assistant"]
MessageStatus = Literal["queued", "running", "done", "failed"]


class Conversation(IdMixin, TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("lens IN ('business', 'marketing', 'finance')", name="lens"),
        Index("ix_conversations_org_id_user_id_last", "org_id", "user_id", "last_message_at"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    lens: Mapped[str] = mapped_column(String(16), nullable=False, default="business")
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Message(IdMixin, TimestampMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="role"),
        CheckConstraint("status IN ('queued', 'running', 'done', 'failed')", name="status"),
        Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # The question's text, or the answer's lead sentence (what the history and follow-ups see).
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="done")
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en")
    lens: Mapped[str] = mapped_column(String(16), nullable=False, default="business")
    answer_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    progress_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    tool_trace: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    quality_warnings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    token_usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(40))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    dictionary_version: Mapped[int | None] = mapped_column(Integer)
    # An answer reused from an identical question asked moments ago (no AI call, nothing counted).
    reused_from: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SavedQuestion(IdMixin, TimestampMixin, Base):
    __tablename__ = "saved_questions"
    __table_args__ = (
        CheckConstraint("lens IN ('business', 'marketing', 'finance')", name="lens"),
        Index("ix_saved_questions_org_id_user_id", "org_id", "user_id"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(String(1000), nullable=False)
    lens: Mapped[str] = mapped_column(String(16), nullable=False, default="business")
