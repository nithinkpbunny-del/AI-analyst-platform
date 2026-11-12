"""Request and response bodies for the Ask screen: conversations, answers and saved questions (M8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LensName = Literal["business", "marketing", "finance"]
QUESTION_MAX = 2000


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _clean(value: str) -> str:
    text = " ".join(value.split())
    if not text:
        raise ValueError("Write a question.")
    return text


class AskRequest(_Body):
    question: str = Field(min_length=1, max_length=QUESTION_MAX)
    lens: LensName = "business"

    @field_validator("question")
    @classmethod
    def _tidy(cls, value: str) -> str:
        return _clean(value)


class FollowUpRequest(_Body):
    question: str = Field(min_length=1, max_length=QUESTION_MAX)

    @field_validator("question")
    @classmethod
    def _tidy(cls, value: str) -> str:
        return _clean(value)


class ConversationPatch(_Body):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    lens: LensName | None = None


class SavedQuestionRequest(_Body):
    text: str = Field(min_length=1, max_length=1000)
    lens: LensName = "business"

    @field_validator("text")
    @classmethod
    def _tidy(cls, value: str) -> str:
        return _clean(value)


class MessageOut(BaseModel):
    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    status: Literal["queued", "running", "done", "failed"]
    lens: LensName
    created_at: datetime
    finished_at: datetime | None
    # The structured answer: lead, key_figure, evidence, why_it_matters, recommendation, data_notes, notes
    # (codes written by the system), next_questions, charts, confidence, cited.
    answer: dict[str, Any] | None
    # Steps while the answer is being made ({"code": "fetching", "table": ..., "connection": ...}).
    progress: list[dict[str, Any]]
    # "Show my work": each tool call with its input, result id, tables and SQL.
    tool_trace: list[dict[str, Any]]
    # result id -> where its figures come from (tables with connection and fetch time, query, rows).
    provenance: dict[str, Any]
    quality_warnings: list[dict[str, Any]]
    error_code: str | None
    reused: bool


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    lens: LensName
    created_at: datetime
    last_message_at: datetime


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]


class ConversationList(BaseModel):
    conversations: list[ConversationOut]


class SavedQuestionOut(BaseModel):
    id: uuid.UUID
    text: str
    lens: LensName
    created_at: datetime


class SavedQuestionList(BaseModel):
    questions: list[SavedQuestionOut]
